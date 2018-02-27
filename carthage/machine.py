import os, os.path
from .dependency_injection import *
from .config import ConfigLayout
from .ssh import SshKey, SshAgent
from .utils import memoproperty
from . import sh
import carthage.ssh

class MachineRunning:

    async def __aenter__(self):
        self.machine.with_running_count +=1
        if self.machine.running:
            return
        try:
            await self.machine.start_machine()
            return

        except:
            self.machine.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.machine.with_running_count -= 1
        if self.machine.with_running_count <= 0:
            self.machine_with_running_count = 0
            await self.machine.stop_machine()


    def __init__(self, machine):
        self.machine = machine

ssh_origin = InjectionKey('ssh-origin')
class SshMixin:
    '''An Item that can be sshed to.  Will look for the ssh_origin
    injection key.  If found, this should be a container.  The ssh will be
    launched from within the network namespace of that container in order
    to reach the appropriate devices.  Requires ip_address to be made
    available.  Requires an carthage.ssh.SshKey be injectable.
    '''

    @property
    def ip_address(self):
        raise NotImplementedError

    ssh_options = ('-oStrictHostKeyChecking=no', )

    @memoproperty
    def ssh(self):
        try:
            ssh_origin_container = self.injector.get_instance(ssh_origin)
        except KeyError:
            ssh_origin_container = self if isinstance(self, Container) else None
        ssh_key = self.injector.get_instance(carthage.ssh.SshKey)
        options = self.ssh_options + ('-oUserKnownHostsFile='+os.path.join(self.config_layout.state_dir, 'ssh_known_hosts'),)
        if ssh_origin_container is not None:
            leader = ssh_origin_container.container_leader
            ssh_origin_container.done_future().add_done_callback(self.ssh_recompute)
            return sh.nsenter.bake('-t', str(leader), "-n",
                                   "/usr/bin/ssh",
                              "-i", ssh_key.key_path,
                                   *options,
                                   self.ip_address,
                                   _env = ssh_key.agent.agent_environ)
        else:
            return sh.ssh.bake('-i', ssh_key.key_path,
                               *options, self.ip_address,
                               _env = ssh_key.agent.agent_environ)

    def ssh_recompute(self):
        try:
            del self.__dict__['ssh']
        except KeyError: pass

    @classmethod
    def clear_ssh_known_hosts(cls, config_layout):
        try: os.unlink(
                os.path.join(config_layout.state_dir, "ssh_known_hosts"))
        except FileNotFoundError: pass

    def ssh_rekeyed(self):
        "Indicate that this host has been rekeyed"
        try:
            self.ip_address
        except NotImplementedError: return
        try: sh.ssh_keygen(
                "-R", self.ip_address,
                f=os.path.join(self.config_layout.state_dir, "ssh_known_hosts"))
        except sh.ErrorReturnCode: pass
        
        
class Machine(AsyncInjectable, SshMixin):

    def __init__(self, name, injector, config_layout):
        super().__init__(injector = injector)
        self.name = name
        self.config_layout = config_layout
        self.injector = injector.copy_if_owned().claim()
        self.ainjector = self.injector(AsyncInjector)
        self.machine_running = MachineRunning(self)
        self.with_running_count = 0


    @property
    def full_name(self):
        return self.config_layout.container_prefix+self.name


    def start_machine(self):
        raise NotImplementedError

    def stop_machine(self):
        raise NotImplementedError
    

__all__ = ['Machine', 'MachineRunning', 'SshMixin']
