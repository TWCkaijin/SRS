from srs.adapters.demo import DemoAdapter
from srs.adapters.slurm import SlurmAdapter
from srs.adapters.ssh import SSHAdapter
from srs.transport import failure


class CompositeAdapter:
    def __init__(self, mode, credentials_file=None):
        self.ssh = SSHAdapter(mode, credentials_file)
        self.slurm = SlurmAdapter(mode, credentials_file)

    async def run_step(self, step, context, emit):
        kind = step.get("kind")
        if kind == "ssh":
            return await self.ssh.run_step(step, context, emit)
        if kind == "slurm_api":
            return await self.slurm.run_step(step, context, emit)
        return failure("Unsupported action kind; no command was dispatched: " + str(kind), exit_code=65)


__all__ = ["CompositeAdapter", "DemoAdapter", "SSHAdapter", "SlurmAdapter"]
