#!/usr/bin/env bash
# Tidybot Universe — hardware setup (no sim)
#
# Clone all repos, create conda env, install everything for running on the
# physical robot (Franka Panda + Robotiq gripper + RealSense cameras + Tidybot base).
#
# Usage:
#   bash setup-hardware.sh ENV_NAME [WORKSPACE_DIR]
#
# Examples:
#   bash setup-hardware.sh xbot ~/xbot
#   bash setup-hardware.sh tidybot              # workspace = ./tidybot_uni
#
# Or download and run directly:
#   curl -fsSL https://raw.githubusercontent.com/TidyBot-Services/Tidybot-Universe/master/setup-hardware.sh | bash -s -- ENV_NAME [WORKSPACE_DIR]
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
  echo "Error: conda env name required." >&2
  echo "Usage: bash setup-hardware.sh ENV_NAME [WORKSPACE_DIR]" >&2
  echo "  ENV_NAME       Name for the new conda environment" >&2
  echo "  WORKSPACE_DIR  Where to clone repos (default: ./tidybot_uni)" >&2
  exit 1
fi

ENV_NAME="$1"
WORKSPACE="${2:-./tidybot_uni}"
WORKSPACE="$(mkdir -p "$WORKSPACE" && cd "$WORKSPACE" && pwd)"

ORG="https://github.com/TidyBot-Services"
PIP="$HOME/miniforge3/envs/$ENV_NAME/bin/pip"

echo "==> Workspace: $WORKSPACE"
echo "==> Conda env: $ENV_NAME"
echo ""

# ── Clone repos ───────────────────────────────────────────────────────────────

clone() {
  local repo="$1" dest="$2"
  if [ -d "$dest/.git" ]; then
    echo "    skip $dest (already cloned)"
  else
    echo "    clone $repo → $dest"
    git clone --depth 1 -q "$ORG/$repo.git" "$dest"
  fi
}

echo "==> Cloning core repos ..."
clone Tidybot-Universe                     "$WORKSPACE/Tidybot-Universe"
clone TidyBot-Services.github.io           "$WORKSPACE/TidyBot-Services.github.io"
clone agent_server                         "$WORKSPACE/agent_server"
clone system_logger                        "$WORKSPACE/system_logger"
clone common                               "$WORKSPACE/common"

# Protocol packages (shared client + message definitions)
mkdir -p "$WORKSPACE/protocols"
clone franka-protocol                      "$WORKSPACE/protocols/franka_protocol"
clone gripper-protocol                     "$WORKSPACE/protocols/gripper_protocol"
clone camera-protocol                      "$WORKSPACE/protocols/camera_protocol"

# ── Hardware service repos ────────────────────────────────────────────────────

echo "==> Cloning hardware service repos ..."
mkdir -p "$WORKSPACE/hardware"
clone arm_franka_service                   "$WORKSPACE/hardware/arm_franka_service"
clone gripper_robotiq_service              "$WORKSPACE/hardware/gripper_robotiq_service"
clone base_tidybot_service                 "$WORKSPACE/hardware/base_tidybot_service"
clone camera_realsense_service             "$WORKSPACE/hardware/camera_realsense_service"

# Standard interface symlinks (agent_server expects these names)
echo "==> Creating hardware symlinks ..."
ln -sfn arm_franka_service           "$WORKSPACE/hardware/arm_server"
ln -sfn gripper_robotiq_service      "$WORKSPACE/hardware/gripper_server"
ln -sfn base_tidybot_service         "$WORKSPACE/hardware/base_server"
ln -sfn camera_realsense_service     "$WORKSPACE/hardware/camera_server"

# ── Symlinks (from common/) ──────────────────────────────────────────────────

echo "==> Setting up common symlinks ..."
[ -f "$WORKSPACE/common/setup.sh" ] && (cd "$WORKSPACE" && bash common/setup.sh)

# ── Conda env ─────────────────────────────────────────────────────────────────

echo "==> Creating conda env '$ENV_NAME' (Python 3.11) ..."
conda create -n "$ENV_NAME" -y python=3.11 -c conda-forge

# ── Pip: shared packages ─────────────────────────────────────────────────────

echo "==> Installing shared pip packages ..."
$PIP install \
  torch==2.10.0 \
  numpy==1.26.4 \
  transforms3d==0.4.2 \
  \
  "h5py>=3.16" \
  "lxml>=6.0" \
  "trimesh>=4.11" \
  "rtree>=1.4" \
  \
  "fastapi>=0.135.0" \
  "uvicorn[standard]>=0.41.0" \
  "pyzmq>=27.0" \
  "msgpack>=1.1" \
  "websockets>=16.0" \
  "httpx>=0.28.0" \
  "httpx-sse>=0.4.0" \
  "pydantic>=2.12" \
  "pydantic-settings>=2.13" \
  "python-multipart>=0.0.22" \
  "python-dotenv>=1.2" \
  "sse-starlette>=3.3" \
  \
  "opencv-python>=4.11" \
  "pillow>=12.1" \
  "imageio>=2.37" \
  "imageio-ffmpeg>=0.6" \
  \
  "scipy>=1.17" \
  "matplotlib>=3.10" \
  "requests>=2.32" \
  "pyyaml>=6.0" \
  "tqdm>=4.67" \
  "psutil>=7.2" \
  "tabulate>=0.10" \
  "rich>=14.3" \
  "tyro>=1.0" \
  "typer>=0.24" \
  "click>=8.3" \
  "GitPython>=3.1" \
  "huggingface_hub>=1.7" \
  "jsonschema>=4.26" \
  "sympy>=1.14" \
  "networkx>=3.6" \
  "cloudpickle>=3.1" \
  "dacite>=1.9" \
  \
  "PyJWT>=2.12" \
  "cryptography>=46.0" \
  "ipython>=9.10" \
  "setuptools<81" \
  claude-agent-sdk

# ── Pip: hardware-specific packages ──────────────────────────────────────────

echo "==> Installing hardware-specific pip packages ..."
$PIP install \
  phoenix6==26.0.0b1 \
  "ruckig>=0.9.0" \
  "threadpoolctl>=3.0.0" \
  "pyserial>=3.5" \
  "minimalmodbus>=2.0.0" \
  "pyrealsense2>=2.50.0" \
  "aiohttp>=3.8.0"

# ── Pip: pybind11 (needed for pylibfranka build) ─────────────────────────────

echo "==> Installing pybind11 ..."
$PIP install "pybind11>=2.11"

# ── Build pylibfranka (Franka C++ bindings for Python) ────────────────────────

LIBFRANKA_DIR="$WORKSPACE/hardware/arm_franka_service/libfranka"
PYTHON3="$HOME/miniforge3/envs/$ENV_NAME/bin/python3"
PYBIND11_CMAKE="$($PYTHON3 -c 'import pybind11; print(pybind11.get_cmake_dir())')"

echo "==> Building pylibfranka for Python 3.11 ..."
rm -rf "$LIBFRANKA_DIR/build"
mkdir -p "$LIBFRANKA_DIR/build"

# Tag needed for version detection
cd "$WORKSPACE/hardware/arm_franka_service"
git tag 0.9.1 2>/dev/null || true

cd "$LIBFRANKA_DIR/build"
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DGENERATE_PYLIBFRANKA=ON \
  -DBUILD_TESTS=OFF \
  -DBUILD_EXAMPLES=OFF \
  -DPython3_EXECUTABLE="$PYTHON3" \
  -Dpybind11_DIR="$PYBIND11_CMAKE"
make -j"$(nproc)"

# Copy .so and install as pip package
SO_FILE=$(find "$LIBFRANKA_DIR/build/pylibfranka" -name "_pylibfranka*.so" | head -1)
cp "$SO_FILE" "$LIBFRANKA_DIR/pylibfranka/"
$PIP install "$LIBFRANKA_DIR/pylibfranka"

echo "==> Verifying pylibfranka ..."
$PYTHON3 -c "from pylibfranka._pylibfranka import Robot; print('    pylibfranka OK')"

# ── Editable local packages ──────────────────────────────────────────────────

echo "==> Installing local packages (editable) ..."
$PIP install --no-deps \
  -e "$WORKSPACE/common" \
  -e "$WORKSPACE/protocols/franka_protocol" \
  -e "$WORKSPACE/protocols/gripper_protocol" \
  -e "$WORKSPACE/protocols/camera_protocol" \
  -e "$WORKSPACE/system_logger"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Hardware setup complete!"
echo "============================================"
echo ""
echo "Activate the environment:"
echo ""
echo "  conda activate $ENV_NAME"
echo ""
echo "Start the robot (two-terminal approach):"
echo ""
echo "  # Terminal 1 — backend services"
echo "  cd $WORKSPACE"
echo "  ./start_robot.sh --no-controller"
echo ""
echo "  # Terminal 2 — API server"
echo "  cd $WORKSPACE/agent_server"
echo "  python server.py --no-service-manager"
echo ""
echo "Or single-terminal with service manager dashboard:"
echo ""
echo "  cd $WORKSPACE/agent_server"
echo "  python server.py"
echo "  # Then open http://localhost:8080/services/dashboard"
echo ""
echo "============================================"
