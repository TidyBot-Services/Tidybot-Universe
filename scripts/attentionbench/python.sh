# Sourced by the entry scripts: start under the system Python, ignoring the caller's Conda, venv and PYTHON* settings.
attentionbench_python() {
  local py=/usr/bin/python3
  [ -x "$py" ] || py=$(command -v python3) || { echo 'python3 is missing: sudo apt-get install python3' >&2; exit 1; }
  exec "$py" -I "$@"
}
