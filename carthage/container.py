import asyncio, logging, os, re, shutil, sys
from .dependency_injection import inject, AsyncInjectable, InjectionKey, Injector, AsyncInjector
from .image import BtrfsVolume, ImageVolume, SetupTaskMixin
from . import sh, ConfigLayout
import carthage.network

logger = logging.getLogger('carthage.container')


class ContainerRunning:

    async def __aenter__(self):
        self.container.with_running_count +=1
        if self.container.running:
            return
        try:
            await self.container.start_container()
            return

        except:
            self.container.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.container.with_running_count -= 1
        if self.container.with_running_count <= 0:
            self.container_with_running_count = 0
            await self.container.stop_container()


    def __init__(self, container):
        self.container = container

container_image = InjectionKey('container-image')
container_volume = InjectionKey('container-volume')

@inject(image = container_image,
        loop = asyncio.AbstractEventLoop,
        config_layout = ConfigLayout,
        network_config = carthage.network.NetworkConfig,
        injector = Injector)
class Container(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, *, config_layout, image, injector, loop, network_config):
        super().__init__(injector = injector)
        self.loop = loop
        self.process = None
        self.name = name
        self.injector = Injector(injector)
        self.image = image
        self.config_layout = config_layout
        self.with_running_count = 0
        self.running = False
        self._operation_lock = asyncio.Lock()
        self._out_selectors = []
        self._done_waiters = []
        self.container_running = ContainerRunning(self)
        self.network_interfaces = []
        self.ainjector = injector(AsyncInjector)

        
        

    async def async_ready(self):
        try: vol = await self.ainjector.get_instance_async(container_volume)
        except KeyError:
            vol = await self.ainjector(BtrfsVolume,
                              clone_from = self.image,
                              name = "containers/"+self.name)
            self.injector.add_provider(container_volume, vol)
        self.volume = vol
        await self.run_setup_tasks()
        network_config_unresolved = await self.ainjector(carthage.network.NetworkConfig)
        self.network_config = await self.ainjector(network_config_unresolved.resolve)
        
        return self

    @property
    def stamp_path(self):
        if self.volume is None:
            raise RuntimeError('Volume not yet created')
        return self.volume.path

    @property
    def full_name(self):
        return self.config_layout.container_prefix+self.name

    async def network_config(self, networking):
        if networking:
            ainjector = self.injector(AsyncInjector)
            net = await ainjector.get_instance_async(carthage.network.Network)
            interface = net.add_veth(self.name)
            self.network_interfaces.append(interface)
            return ['--network-interface={}'.format(interface.ifname)]
        else:
            try: os.unlink(os.path.join(self.volume.path, "etc/resolv.conf"))
            except FileNotFoundError: pass
            shutil.copyfile("/etc/resolv.conf",
                            os.path.join(self.volume.path, "etc/resolv.conf"))
            return []
    
    async def run_container(self, *args, raise_on_running = True,
                            networking = False,
                            as_pid2 = True):
        async with self._operation_lock:
            if self.running:
                if raise_on_running:
                    raise RuntimeError('{} already running'.format(self))
                return self.process
            ns_args = await self.network_config(networking)
            if as_pid2:
                ns_args.append('--as-pid2')
            logger.info("Starting container {}: {}".format(
                self.name,
                " ".join(args)))
            self.process = sh.systemd_nspawn("--directory="+self.volume.path,
                                             '--machine='+self.full_name,
                                             "--setenv=DEBIAN_FRONTEND=noninteractive",
                                             *ns_args,
                                             *args,
                                             _bg = True,
                                             _bg_exc = False,
                                             _done = self._done_cb,
                                             _out = self._out_cb,
                                             _err_to_out = True,
                                             _tty_out = True,
                                             _in = "/dev/null",
                                             _encoding = 'utf-8',
                                             _new_session = False
                                             )
            
            self.running = True
            return self.process

    async def stop_container(self):
        async with self._operation_lock:
            if not self.running:
                raise RuntimeError("Container not running")
            self.process.terminate()
            process = self.process
            self.process = None
            await process

    def _done_cb(self, cmd, success, code):
        def callback():
            # Callback needed to run in IO loop thread because futures
            # do not trigger their done callbacks in a threadsafe
            # manner.
            for f in self._done_waiters:
                if not f.cancelled():
                    f.set_result(code)
            self._done_waiters = []
            for i in self.network_interfaces:
                i.close()
            self.network_interfaces = []

        logger.info("Container {} exited with code {}".format(
            self.name, code))
        self.running = False
        self.loop.call_soon_threadsafe(callback)

    def done_future(self):
        future = self.loop.create_future()
        self._done_waiters.append(future)
        return future
    
    def _out_cb(self, data):
        data = data.strip()
        logger.debug("Container {}: output {}".format(self. name,
                                                      data))

        
        for selector in self._out_selectors:
            r, cb, once = selector
            if cb is None: continue
            m = r.search(data)
            if m:
                try:
                    self.loop.call_soon_threadsafe(cb, m, data)
                except Exception:
                    logger.exception("Container {}: Error calling {}".format(
                        self.name, cb))
                if once:
                    # Free the RE and callback
                    selector[0:2] = [None, None]
                    
    def find_output(self, regexp, cb, once):
        regexp = re.compile(regexp)
        assert isinstance(once, bool)
        self._out_selectors.append([regexp, cb, once])

    async def start_container(self, *args):
        def started_callback(m, data):
            started_future.set_result(True)
        if self.running: return
        started_future = self.loop.create_future()
        self.find_output(r'\] Reached target Basic System', started_callback, True)
        await self.run_container("--boot", *args,
                                 networking = True, as_pid2 = False)
        done_future = self.done_future()
        await asyncio.wait([done_future, started_future],
                           loop = self.loop,
                           return_when = "FIRST_COMPLETED")
        if done_future.done():
            logger.error("Container {} failed to start".format(self.name))
            raise RuntimeError("Container failed to start")
        assert started_future.result() is True
        logger.info("Container {} started".format(self.name))

    def close(self):
        if self.process is not None:
            try: self.process.terminate()
            except Exception: pass
            self.process = None
        if hasattr(self, 'volume'):
            self.volume.close()
            del self.volume

    def __del__(self):
        self.close()

    async def network_online(self):
        await self.shell('/bin/systemctl', "start", "network-online.target",
                         _bg = True, _bg_exc = False
                         )

        
    @property
    def shell(self):
        if not self.running:
            raise RuntimeError("Container not running")
        leader = str(sh.machinectl('-pLeader', '--value', 'show', self.full_name,
                                   _in = "/dev/null",
                                   _tty_out = False,
                                   ).stdout,
                     'utf-8').strip()
        return sh.nsenter.bake( "-t"+leader, "-C", "-m", "-n", "-u", "-i", "-p",
                                _env = self._environment())

    def _environment(self):
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        return env
