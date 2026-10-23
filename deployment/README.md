# Linux execution dependencies

The plugin is standalone relative to Hermes source. Sandbox and host/root execution additionally
require a root-owned OS broker, Docker, a built image, isolated egress rules and constrained sudo.
These files are installation templates, not automatic installers. Replace the task-root path,
service UID, skills paths and immutable image ID in hermes-qq-exec.py before root installs it.
Never grant the service unrestricted sudo or mount the broker/socket inside a task container.
Review subnet conflicts before installing hermes-qq-network.py; it manages a dedicated network only.
Existing deployments should reuse their tested broker and image instead of rebuilding.
Do not install unresolved placeholders. Run visudo validation and a real sandbox probe.
