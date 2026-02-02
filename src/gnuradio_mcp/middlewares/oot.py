from __future__ import annotations

import io
import json
import logging
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gnuradio_mcp.models import ComboImageInfo, ComboImageResult, OOTImageInfo, OOTInstallResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_IMAGE = "gnuradio-runtime:latest"

DOCKERFILE_TEMPLATE = """\
FROM {base_image}

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential cmake git \\
    {extra_build_deps}\\
    && rm -rf /var/lib/apt/lists/*

# Clone and build
WORKDIR /build
COPY fix_binding_hashes.py /tmp/fix_binding_hashes.py
RUN git clone --depth 1 --branch {branch} {git_url} && \\
    cd {repo_dir} && \\
    python3 /tmp/fix_binding_hashes.py . && \\
    mkdir build && cd build && \\
    cmake -DCMAKE_INSTALL_PREFIX=/usr {cmake_args}.. && \\
    make -j$(nproc) && make install && \\
    ldconfig && \\
    rm -rf /build

WORKDIR /flowgraphs

# Bridge Python site-packages (cmake installs to versioned path)
ENV PYTHONPATH="/usr/lib/python3.11/site-packages:${{PYTHONPATH}}"
"""

# Standalone script injected into OOT Docker builds to fix stale
# pybind11 binding hashes that would otherwise trigger castxml regen.
FIX_BINDING_HASHES_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"Fix stale BINDTOOL_HEADER_FILE_HASH in pybind11 binding files.

GNU Radio's GR_PYBIND_MAKE_OOT cmake macro compares MD5 hashes of C++
headers against values stored in the binding .cc files.  When they
differ it tries to regenerate via castxml, which often fails in minimal
Docker images.  This script updates the hashes to match the actual
headers so cmake skips the regeneration step.
\"\"\"
import hashlib, pathlib, re, sys

root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")

# GR 3.9-: python/bindings/  |  GR 3.10+: python/<module>/bindings/
binding_dirs = list(root.joinpath("python").glob("**/bindings"))
if not binding_dirs:
    sys.exit(0)

for bindings in binding_dirs:
    for cc in sorted(bindings.glob("*_python.cc")):
        text = cc.read_text()
        m = re.search(r"BINDTOOL_HEADER_FILE\\((\\S+)\\)", text)
        if not m:
            continue
        header = next(root.joinpath("include").rglob(m.group(1)), None)
        if not header:
            continue
        actual = hashlib.md5(header.read_bytes()).hexdigest()
        new_text = re.sub(
            r"BINDTOOL_HEADER_FILE_HASH\\([a-f0-9]+\\)",
            f"BINDTOOL_HEADER_FILE_HASH({actual})",
            text,
        )
        if new_text != text:
            cc.write_text(new_text)
            print(f"Fixed binding hash: {cc.name}")
"""


class OOTInstallerMiddleware:
    """Builds OOT modules into Docker images from git repos.

    Each call to build_module() generates a Dockerfile matching the
    pattern in docker/Dockerfile.gnuradio-lora-runtime, builds it,
    and registers the result in a persistent JSON registry.
    """

    def __init__(
        self,
        docker_client: Any,
        base_image: str = DEFAULT_BASE_IMAGE,
    ):
        self._client = docker_client
        self._base_image = base_image
        self._registry_path = Path.home() / ".gr-mcp" / "oot-registry.json"
        self._registry: dict[str, OOTImageInfo] = self._load_registry()
        self._combo_registry_path = Path.home() / ".gr-mcp" / "oot-combo-registry.json"
        self._combo_registry: dict[str, ComboImageInfo] = self._load_combo_registry()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def build_module(
        self,
        git_url: str,
        branch: str = "main",
        build_deps: list[str] | None = None,
        cmake_args: list[str] | None = None,
        base_image: str | None = None,
        force: bool = False,
    ) -> OOTInstallResult:
        """Build Docker image with an OOT module compiled in."""
        effective_base = base_image or self._base_image

        try:
            module_name = self._module_name_from_url(git_url)
            commit = self._get_remote_commit(git_url, branch)
            image_tag = f"gr-oot-{module_name}:{branch}-{commit}"

            # Idempotent: skip if image already exists
            if not force and self._image_exists(image_tag):
                existing = self._registry.get(module_name)
                return OOTInstallResult(
                    success=True,
                    image=existing,
                    skipped=True,
                )

            # Generate and build
            dockerfile = self.generate_dockerfile(
                git_url=git_url,
                branch=branch,
                base_image=effective_base,
                build_deps=build_deps,
                cmake_args=cmake_args,
            )

            log_lines = self._docker_build(dockerfile, image_tag)
            build_log_tail = "\n".join(log_lines[-30:])

            # Register
            info = OOTImageInfo(
                module_name=module_name,
                image_tag=image_tag,
                git_url=git_url,
                branch=branch,
                git_commit=commit,
                base_image=effective_base,
                built_at=datetime.now(timezone.utc).isoformat(),
            )
            self._registry[module_name] = info
            self._save_registry()

            return OOTInstallResult(
                success=True,
                image=info,
                build_log_tail=build_log_tail,
            )

        except Exception as e:
            logger.exception("OOT module build failed")
            return OOTInstallResult(
                success=False,
                error=str(e),
            )

    def list_images(self) -> list[OOTImageInfo]:
        """List all registered OOT module images."""
        return list(self._registry.values())

    def remove_image(self, module_name: str) -> bool:
        """Remove Docker image and registry entry."""
        info = self._registry.pop(module_name, None)
        if info is None:
            return False

        try:
            self._client.images.remove(info.image_tag, force=True)
        except Exception as e:
            logger.warning("Failed to remove Docker image %s: %s", info.image_tag, e)

        self._save_registry()
        return True

    # ──────────────────────────────────────────
    # Dockerfile Generation
    # ──────────────────────────────────────────

    def generate_dockerfile(
        self,
        git_url: str,
        branch: str,
        base_image: str,
        build_deps: list[str] | None = None,
        cmake_args: list[str] | None = None,
    ) -> str:
        """Generate a Dockerfile string for building an OOT module."""
        repo_dir = self._repo_dir_from_url(git_url)

        extra_deps = ""
        if build_deps:
            extra_deps = " ".join(build_deps) + " "

        cmake_flags = ""
        if cmake_args:
            cmake_flags = " ".join(cmake_args) + " "

        return DOCKERFILE_TEMPLATE.format(
            base_image=base_image,
            branch=branch,
            git_url=git_url,
            repo_dir=repo_dir,
            extra_build_deps=extra_deps,
            cmake_args=cmake_flags,
        )

    # ──────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _module_name_from_url(url: str) -> str:
        """Extract module name from git URL.

        "https://github.com/tapparelj/gr-lora_sdr.git" -> "lora_sdr"
        "https://github.com/osmocom/gr-osmosdr" -> "osmosdr"
        "https://github.com/gnuradio/volk.git" -> "volk"
        """
        # Strip trailing .git and slashes
        cleaned = url.rstrip("/")
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        # Get last path segment
        name = cleaned.rsplit("/", 1)[-1]
        # Strip gr- prefix if present
        if name.startswith("gr-"):
            name = name[3:]
        return name

    @staticmethod
    def _repo_dir_from_url(url: str) -> str:
        """Get the directory name git clone will create.

        "https://github.com/tapparelj/gr-lora_sdr.git" -> "gr-lora_sdr"
        """
        cleaned = url.rstrip("/")
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        return cleaned.rsplit("/", 1)[-1]

    @staticmethod
    def _get_remote_commit(git_url: str, branch: str) -> str:
        """Get latest commit hash from remote without cloning."""
        result = subprocess.run(
            ["git", "ls-remote", git_url, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git ls-remote failed: {result.stderr.strip()}")
        output = result.stdout.strip()
        if not output:
            raise RuntimeError(
                f"Branch '{branch}' not found in {git_url}"
            )
        return output.split()[0][:7]

    def _image_exists(self, tag: str) -> bool:
        """Check if a Docker image with this tag exists locally."""
        try:
            self._client.images.get(tag)
            return True
        except Exception:
            return False

    @staticmethod
    def _build_context(dockerfile: str) -> io.BytesIO:
        """Create a tar archive build context with Dockerfile and helper scripts."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, content in [
                ("Dockerfile", dockerfile),
                ("fix_binding_hashes.py", FIX_BINDING_HASHES_SCRIPT),
            ]:
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        return buf

    def _docker_build(self, dockerfile: str, tag: str) -> list[str]:
        """Build a Docker image from a Dockerfile string. Returns log lines."""
        context = self._build_context(dockerfile)
        log_lines: list[str] = []
        try:
            _image, build_log = self._client.images.build(
                fileobj=context,
                custom_context=True,
                tag=tag,
                rm=True,
                forcerm=True,
            )
            for chunk in build_log:
                if "stream" in chunk:
                    line = chunk["stream"].rstrip("\n")
                    if line:
                        log_lines.append(line)
                        logger.debug("build: %s", line)
        except Exception as e:
            raise RuntimeError(f"Docker build failed: {e}") from e
        return log_lines

    def _load_registry(self) -> dict[str, OOTImageInfo]:
        """Load the OOT image registry from disk.

        Validates entries individually so one corrupted entry
        doesn't discard the entire registry.
        """
        if not self._registry_path.exists():
            return {}
        try:
            data = json.loads(self._registry_path.read_text())
        except Exception as e:
            logger.warning("Failed to parse OOT registry JSON: %s", e)
            return {}
        registry: dict[str, OOTImageInfo] = {}
        for k, v in data.items():
            try:
                registry[k] = OOTImageInfo(**v)
            except Exception as e:
                logger.warning("Skipping corrupt registry entry '%s': %s", k, e)
        return registry

    def _save_registry(self) -> None:
        """Persist the OOT image registry to disk."""
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: v.model_dump()
            for k, v in self._registry.items()
        }
        self._registry_path.write_text(json.dumps(data, indent=2))

    # ──────────────────────────────────────────
    # Combo Image (Multi-OOT) Support
    # ──────────────────────────────────────────

    @staticmethod
    def _combo_key(module_names: list[str]) -> str:
        """Deterministic key from module names: sorted, deduped, joined."""
        names = sorted(set(module_names))
        return "combo:" + "+".join(names)

    @staticmethod
    def _combo_image_tag(module_names: list[str]) -> str:
        """Deterministic image tag for a combo of modules."""
        names = sorted(set(module_names))
        return f"gr-combo-{'-'.join(names)}:latest"

    def generate_combo_dockerfile(self, module_names: list[str]) -> str:
        """Generate multi-stage Dockerfile that COPYs from existing single-OOT images."""
        names = sorted(set(module_names))
        stages: list[str] = []
        copies: list[str] = []

        for name in names:
            info = self._registry.get(name)
            if info is None:
                raise ValueError(
                    f"Module '{name}' not found in OOT registry. "
                    f"Build it first with build_module()."
                )
            stage_alias = f"stage_{name}"
            stages.append(f"FROM {info.image_tag} AS {stage_alias}")
            for path in ["/usr/lib/", "/usr/include/", "/usr/share/gnuradio/"]:
                copies.append(f"COPY --from={stage_alias} {path} {path}")

        return "\n".join([
            *stages,
            "",
            f"FROM {self._base_image}",
            "",
            *copies,
            "",
            "RUN ldconfig",
            "WORKDIR /flowgraphs",
            'ENV PYTHONPATH="/usr/lib/python3.11/site-packages:${PYTHONPATH}"',
            "",
        ])

    def build_combo_image(
        self,
        module_names: list[str],
        force: bool = False,
    ) -> ComboImageResult:
        """Build a combined Docker image with multiple OOT modules.

        Modules already in the registry are used as-is. Modules found
        in the OOT catalog but not yet built are auto-built first.
        """
        from gnuradio_mcp.oot_catalog import CATALOG

        names = sorted(set(module_names))
        if len(names) < 2:
            return ComboImageResult(
                success=False,
                error="At least 2 distinct modules required for a combo image.",
            )

        combo_key = self._combo_key(names)
        image_tag = self._combo_image_tag(names)

        try:
            # Idempotent: skip if combo already exists
            if not force and self._image_exists(image_tag):
                existing = self._combo_registry.get(combo_key)
                if existing is not None:
                    return ComboImageResult(
                        success=True,
                        image=existing,
                        skipped=True,
                    )

            # Auto-build missing modules from catalog
            modules_built: list[str] = []
            for name in names:
                if name in self._registry:
                    continue
                entry = CATALOG.get(name)
                if entry is None:
                    return ComboImageResult(
                        success=False,
                        error=(
                            f"Module '{name}' is not in the OOT registry and "
                            f"not found in the catalog. Build it manually first "
                            f"with build_module()."
                        ),
                    )
                # Auto-build from catalog
                logger.info("Auto-building '%s' from catalog for combo image", name)
                result = self.build_module(
                    git_url=entry.git_url,
                    branch=entry.branch,
                    build_deps=entry.build_deps or None,
                    cmake_args=entry.cmake_args or None,
                )
                if not result.success:
                    return ComboImageResult(
                        success=False,
                        error=f"Auto-build of '{name}' failed: {result.error}",
                        modules_built=modules_built,
                    )
                modules_built.append(name)

            # Generate and build combo
            dockerfile = self.generate_combo_dockerfile(names)
            log_lines = self._docker_build(dockerfile, image_tag)
            build_log_tail = "\n".join(log_lines[-30:])

            # Collect module infos for the combo record
            module_infos = [self._registry[n] for n in names]

            info = ComboImageInfo(
                combo_key=combo_key,
                image_tag=image_tag,
                modules=module_infos,
                built_at=datetime.now(timezone.utc).isoformat(),
            )
            self._combo_registry[combo_key] = info
            self._save_combo_registry()

            return ComboImageResult(
                success=True,
                image=info,
                build_log_tail=build_log_tail,
                modules_built=modules_built,
            )

        except Exception as e:
            logger.exception("Combo image build failed")
            return ComboImageResult(
                success=False,
                error=str(e),
            )

    def list_combo_images(self) -> list[ComboImageInfo]:
        """List all combined multi-OOT images."""
        return list(self._combo_registry.values())

    def remove_combo_image(self, combo_key: str) -> bool:
        """Remove a combo image by its key (e.g., 'combo:adsb+lora_sdr')."""
        info = self._combo_registry.pop(combo_key, None)
        if info is None:
            return False

        try:
            self._client.images.remove(info.image_tag, force=True)
        except Exception as e:
            logger.warning(
                "Failed to remove combo Docker image %s: %s", info.image_tag, e
            )

        self._save_combo_registry()
        return True

    # ──────────────────────────────────────────
    # Combo Registry Persistence
    # ──────────────────────────────────────────

    def _load_combo_registry(self) -> dict[str, ComboImageInfo]:
        """Load the combo image registry from disk."""
        if not self._combo_registry_path.exists():
            return {}
        try:
            data = json.loads(self._combo_registry_path.read_text())
            return {k: ComboImageInfo(**v) for k, v in data.items()}
        except Exception as e:
            logger.warning("Failed to load combo registry: %s", e)
            return {}

    def _save_combo_registry(self) -> None:
        """Persist the combo image registry to disk."""
        self._combo_registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.model_dump() for k, v in self._combo_registry.items()}
        self._combo_registry_path.write_text(json.dumps(data, indent=2))
