# Copyright (C)  2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import pytest
import shutil

from carthage import become_privileged, modeling, oci, podman , ssh
from carthage import *
from carthage.pytest import *

state_dir = Path(__file__).parent.joinpath("test_state")

@pytest.fixture(scope='session')
def enable_podman():
    import carthage.plugins
    base_injector(carthage.plugins.load_plugin, 'carthage.podman')

@pytest.fixture()
def ainjector(loop, ainjector, enable_podman):
    ainjector = ainjector.claim("test_become.py")
    ainjector.add_provider(ssh.SshKey)
    ainjector.add_provider(ssh.AuthorizedKeysFile)
    ainjector.add_provider(ssh.ssh_agent)
    config = ainjector.injector(ConfigLayout)
    config.state_dir = state_dir
    config.authorized_keys = ""
    state_dir.mkdir(parents=True, exist_ok=True)
    loop.run_until_complete(ainjector.get_instance_async(ssh.SshKey))
    yield ainjector
    shutil.rmtree(state_dir, ignore_errors=True)

class Layout(modeling.CarthageLayout):

    add_provider(modeling.machine_implementation_key, dependency_quote(podman.PodmanContainer))

    @modeling.provides(oci.oci_container_image)
    @inject(base_image=None)
    class image(podman.PodmanImageModel):
        base_image = 'debian:latest'
        oci_image_command = ['/bin/systemd']
        oci_image_tag = 'localhost/debian:with_ssh'
        oci_interactive = True

        class cust(FilesystemCustomization):
            description = 'install software'

            @setup_task('install systemd and ssh')
            async def install_systemd_ssh(self):
                await self.run_command('apt', 'update')
                await self.run_command(
                    'apt', '-y', 'install',
                    'openssh-server',
                    'sudo',
                    'systemd')

                
    class machine(modeling.MachineModel):
        machine_mixins = (become_privileged.BecomePrivilegedMixin, )
        ip_address = '127.0.0.1'
        add_provider(oci.OciExposedPort(22))
        ssh_login_user = 'user'

        class authorize_cust(FilesystemCustomization):
            @setup_task("Create user and authorize")
            @inject(authorized_keys=ssh.AuthorizedKeysFile)
            async def create_user(self, authorized_keys):
                await self.run_command(
                    'useradd', '-m', '-s', '/bin/bash',
                    'user')
                p = self.path/'home/user/.ssh/authorized_keys'
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(authorized_keys.path.read_text())
                await self.run_command('adduser', 'user', 'sudo')
                await self.run_command(
                    'chown', '-R', 'user',
                    '/home/user/.ssh')
                self.path.joinpath('etc/sudoers.d/sudo').write_text('''
%sudo   ALL=(ALL:ALL) NOPASSWD:ALL
''')
                
                                                                


@pytest.fixture()
@async_test
async def layout(ainjector):
    ainjector.add_provider(Layout)
    return await ainjector.get_instance_async(Layout)

@async_test
async def test_become_privileged_mixin(layout):
    ainjector = layout.ainjector
    try:
        await ainjector.get_instance_async(InjectionKey(Machine, host='machine', _ready=True))
        async with layout.machine.machine.machine_running():
            await become_privileged.BecomePrivilegedMixin.run_command(layout.machine.machine, 'touch', '/foo')
            async with Machine.filesystem_access(layout.machine.machine) as path:
                path.joinpath('usr/bin/dpkg').unlink()
    finally:
        try: await layout.machine.machine.delete()
        except Exception: pass
        
