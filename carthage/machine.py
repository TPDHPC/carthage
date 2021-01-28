import asyncio, contextlib, os, os.path, tempfile
from .dependency_injection import *
from .config import ConfigLayout
from .ssh import SshKey, SshAgent, RsyncPath
from .utils import memoproperty
from . import sh
import carthage.ssh
from .setup_tasks import SetupTaskMixin, setup_task

class MachineRunning:

    async def __aenter__(self):
        if self.machine.with_running_count <= 0:
            self.machine.already_running = self.machine.running
        self.machine.with_running_count +=1
        if self.machine.running:
            return
        try:
            await self.machine.start_machine()
            if self.ssh_online: await self.machine.ssh_online()
            return

        except:
            self.machine.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.machine.with_running_count -= 1
        if self.machine.with_running_count <= 0 and not self.machine.already_running:
            self.machine.with_running_count = 0
            await self.machine.stop_machine()


    def __init__(self, machine, *,
                 ssh_online = False):
        self.machine = machine
        self.ssh_online = ssh_online

ssh_origin = InjectionKey('ssh-origin')
ssh_origin_vrf = InjectionKey("ssh-origin-vrf")
class SshMixin:
    '''An Item that can be sshed to.  Will look for the ssh_origin
    injection key.  If found, this should be a container.  The ssh will be
    launched from within the network namespace of that container in order
    to reach the appropriate devices.  Requires ip_address to be made
    available.  Requires an carthage.ssh.SshKey be injectable.
    '''

    class ip_address:

        def __get__(self, instance, owner):
            if instance is None: return self
            raise NotImplementedError
    ip_address = ip_address()
    ssh_options = ('-oStrictHostKeyChecking=no', )

    @memoproperty
    def ssh(self):
        from .network import access_ssh_origin
        try:
            ssh_origin_container = self.injector.get_instance(ssh_origin)
        except InjectionFailed:
            from .container import Container
            ssh_origin_container = self if isinstance(self, Container) else None
        ssh_key = self.injector.get_instance(carthage.ssh.SshKey)
        options = self.ssh_options + ('-oUserKnownHostsFile='+os.path.join(self.config_layout.state_dir, 'ssh_known_hosts'),)
        if ssh_origin_container is not None:
            ip_address = self.ip_address
            ssh_origin_container.done_future().add_done_callback(self.ssh_recompute)
            return self.injector(access_ssh_origin).bake(
                                   "/usr/bin/ssh",
                              "-i", ssh_key.key_path,
                                   *options,
                                   ip_address,
                                   _env = ssh_key.agent.agent_environ)
        else:
            return sh.ssh.bake('-i', ssh_key.key_path,
                               *options, self.ip_address,
                               _env = ssh_key.agent.agent_environ)

    def rsync(self, *args):
        '''
        Call rsync with given arguments.
An argument may be a :class:`.RsyncPath` generated by :meth:`rsync_path`.  Such a path encapsulates a host name and a path.  When *rsync* is called, Carthage finds the appropriate ssh_origin to select the right namespace for rsync.

        Typical usage::

            await machine.rsync("file",
                rsync_path("/etc/script")
            #Copy file to /etc/script on machine
        '''
        ssh_key = self.injector.get_instance(SshKey)
        return ssh_key.rsync(*args)

    def rsync_path(self, p):
        '''
A marker in a call to :meth:`rsync` indicating that *p* should be copied to or from *self*.  Interacts with the Carthage rsync machinery to select the right network namespace.
        '''
        return RsyncPath(self, p)

    async def ssh_online(self):
        online = False
        for i in range(30):
            try: await self.ssh('date',
                                _bg = True, _bg_exc = False,
                                _timeout = 5)
            except (sh.TimeoutException, sh.ErrorReturnCode):
                await asyncio.sleep(1)
                continue
            online = True
            break
        if not online:
            raise TimeoutError("{} not online".format(self.ip_address))
        
    def ssh_recompute(self, *args):
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
        
        
@inject_autokwargs(config_layout = ConfigLayout)
class Machine(AsyncInjectable, SshMixin):

    '''
    Represents a machine that can be interacted with.

    This is an abstract class representing the interface to machines independent of the technology being used to control them.  This class can be used for:

    * :class:`Locally run containers <carthage.container.Container>`

    * :class:`Local VMs <carthage.vm.Vm>` using KVM

    * Vmware :class:`~carthage.vmware.vm.Vm`
    * Containers or VMs run on other infrastructure.

    The main capabilities of this interface are to be able to start and stop machines, know their IP address, and connect via ssh.

    '''

    def __init__(self, name, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.with_running_count = 0
        self.already_running = False
        self.sshfs_count = 0
        self.sshfs_lock = asyncio.Lock()
        self.injector.add_provider(InjectionKey(Machine), self)

    def machine_running(self, **kwargs):
        '''Returns a asynchronous context manager; within the context manager, the machine is expected to be running unless :meth:`stop_machine` is explicitly called.
'''
        return MachineRunning(self, **kwargs)
    

    @property
    def full_name(self):
        return self.config_layout.container_prefix+self.name

    async def start_dependencies(*args, **kwargs):
        '''Interface point that should be called by :meth:`start_machine` to start any dependent machines such as routers needed by this machine.'''
        pass
    

    def start_machine(self):
        '''
        Must be overridden.  Start the machine.
        '''
        raise NotImplementedError

    def stop_machine(self):
        ''' Must be overridden; stop the machine.
        '''
        raise NotImplementedError

    def __repr__(self):
        res =  f"<{self.__class__.__name__} name:{self.name} "
        try:
            res += f"ip_address:{self.ip_address}"
        except Exception: pass
        res += ">"
        return res

    async def apply_customization(self, cust_class, method = 'apply'):
        '''
        Apply a :class:`BaseCustomization` to this machine..
        '''
        customization = await self.ainjector(cust_class, apply_to = self)
        meth = getattr(customization, method)
        return await meth()

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        '''
        An asynchronous context manager that makes the filesystem of the *Machine* available on a local path.

        :returns: Path at which the filesystem can be accessed while in the context.

        '''
        self.sshfs_count += 1
        try:
            # Argument for correctness of locking.  The goal of
            # sshfs_lock is to make sure that two callers are not both
            # trying to spin up sshfs at the same time.  The lock is
            # never held when sshfs_count is < 1, so it will not block
            # when the coroutine that actually starts sshfs acquires
            # the lock.  Therefore the startup can actually proceed.
            # It would be equally correct to grab the lock before
            # incrementing sshfs_count, but more difficult to
            # implement because the lock must be released by time of
            # yield so other callers can concurrently access the filesystem.
            async with self.sshfs_lock:
                if self.sshfs_count == 1:
                    self.sshfs_path = tempfile.mkdtemp(dir = self.config_layout.state_dir, prefix=self.name, suffix = "sshfs")
                    self.sshfs_process = sh.sshfs(
                        '-o' 'ssh_command='+" ".join(
                            str(self.ssh).split()[:-1]) ,
                        f'{self.ip_address}:/',
                        self.sshfs_path,
                        '-f',
                        _bg = True,
                        _bg_exc = False)
                    for x in range(5):
                        await asyncio.sleep(0.4)
                        if os.path.exists(os.path.join(
                                self.sshfs_path, "run")):
                            break
                        alive, *rest = self.sshfs_process.process.is_alive()
                        if not alive:
                            await self.sshfs_process
                            raise RuntimeError #I'd expect that to have happened from an sh exit error already
                    else:
                        raise TimeoutError("sshfs failed to mount")
            yield self.sshfs_path
        finally:
            self.sshfs_count -= 1
            if self.sshfs_count <= 0:
                self.sshfs_count = 0
                try:
                    self.sshfs_process.process.terminate()
                except: pass
                dir = self.sshfs_path
                self.sshfs_path = None
                self.sshfs_process = None
                await asyncio.sleep(0.2)
                with contextlib.suppress(OSError):
                    os.rmdir(dir)

                
                
@inject_autokwargs( config_layout = ConfigLayout)
class BaseCustomization(SetupTaskMixin, AsyncInjectable):

    def __init__(self, apply_to: Machine,
                  **kwargs):
        self.host = apply_to
        super().__init__(**kwargs)

    async def async_ready(self):
        # We do not run setup tasks on construction.
        return await AsyncInjectable.async_ready(self)

    #:Can be overridden; a context manager in which customization tasks should be run
    customization_context = None

    @property
    def stamp_path(self):
        return self.host.stamp_path

    async def last_run(self):
        '''
        :return: the most recent time any setup task on this Customization has run against the given host. Returns false if the tasks definitely need to run.
        '''
        last_run = 0.0
        for t in self.setup_tasks:
            run_task, last_run = await t.should_run_task(self,  last_run, ainjector = self.ainjector)
            if run_task:
                return False #We're a check_completed function not a should_run function
        return last_run

    async def apply(self):
        ''' Run setup tasks against host'''
        return await self.ainjector(self.run_setup_tasks, context = self.customization_context)

    def __getattr__(self, a):
        if a in ('ssh', 'ip_address', 'start_machine', 'stop_machine',
                 "filesystem_access",
                 'name', 'full_name',
                 'apply_customization'):
            return getattr(self.host, a)
        raise AttributeError

    def __repr__(self):
        return f"<{self.__class__.__name__} description:\"{self.description}\" for {self.host.name}>"
    
    #: A description of the customization for inclusion in task logging
    description = ""

class MachineCustomization(BaseCustomization):

    '''A customization class for running customizations on running machines.'''
    
    @property
    def customization_context(self):
        return self.host.machine_running(ssh_online = True)

class ContainerCustomization(BaseCustomization):

    '''A customization class for running tasks on :class:`~carthage.container.Container` instances or :class:`~carthage.image.ImageVolume` instances without actually booting the container.  This is valuable for tasks used in image production that want to manipulate the filesystem.
'''

    def __init__(self, apply_to, **kwargs):
        from .container import Container
        if not isinstance(apply_to, Container):
            raise TypeError(f'{self.__class__.__name__} can only be applied to Containers or ImageVolumes')
        super().__init__(apply_to = apply_to, **kwargs)

    @property
    def path(self):
        return self.host.volume.path
    
    def __getattr__(self, a):
        if a in ('container_command', ):
            return getattr(self.host, a)
        else: return super().__getattr__(a)
        

class FilesystemCustomization(BaseCustomization):

    '''
    A Customization class for interacting with the filesystem either of a :class:`carthage.container.Container`, :class:`ImageVolume` or :class:`Machine`.  If possible (for containers and image volumes), do not actually boot the machine.
'''

    def __init__(self, apply_to, **kwargs):
        from .container import Container
        if isinstance(apply_to, Container):
            self.path = apply_to.volume.path
            run_command = apply_to.container_command
        else:
            self.customization_context = self._machine_context()
            run_command = apply_to.ssh
        #: Run a command on the given filesystem, avoiding a boot if possible
        self.run_command = run_command
        super().__init__(apply_to, **kwargs)

    @contextlib.asynccontextmanager
    async def _machine_context(self):
        async with self.host.filesystem_access() as path:
            self.path = path
            yield
            return
        
            
    
def customization_task    (c: BaseCustomization, order: int = None,
                           before = None):
    '''
    :return: a setup_task for using a particular :class:`Customization` in a given :class:`Machine`.

    Usage::

        # in a machine
        add_packages = customization_task(AddOurPackagesCustomization)

    '''
    @setup_task(c.description, order = order, before = before)
    @inject(ainjector = AsyncInjector)
    async def do_task(machine, ainjector):
        await machine.apply_customization(c)

    @do_task.check_completed()
    @inject(ainjector = AsyncInjector)
    async def do_task(machine, ainjector):
        return await machine.apply_customization(c, method = "last_run")
    return do_task


            

__all__ = ['Machine', 'MachineRunning', 'SshMixin', 'BaseCustomization', 'ContainerCustomization',
           'FilesystemCustomization',
           'MachineCustomization']
