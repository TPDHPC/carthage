from test_helpers import *
import os.path, pytest
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector
from carthage.config import ConfigLayout
from carthage.image import BtrfsVolume, ContainerImage
import posix, gc

    

@pytest.fixture()
def a_injector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    injector = base_injector(AsyncInjector)
    cl = injector.get_instance(InjectionKey(ConfigLayout))
    cl.delete_volumes = True
    yield injector
    gc.collect()


@async_test
async def test_btrfs_volume_base(a_injector, loop):
    cl = await a_injector(ConfigLayout)
    assert not os.path.exists(os.path.join(cl.image_dir, "foo")), "The volume directory already exists"
    v = await a_injector(BtrfsVolume, name = "foo")
    assert isinstance(v, BtrfsVolume)
    assert v.path == os.path.join(cl.image_dir, v.name)
    assert v.name == "foo"
    v.close()
    

@async_test
async def test_btrfs_clone(a_injector, loop):
    vol = await a_injector(BtrfsVolume, name = "foo2")
    assert isinstance(vol, BtrfsVolume)
    with open(os.path.join(vol.path, "bar.txt"), "w") as f:
        f.write("This is a file\n")
    v2 =await a_injector(BtrfsVolume, name = "clone_foo2", clone_from = vol)
    assert os.path.exists(os.path.join(v2.path, "bar.txt"))
    

@async_test
async def test_image_unpack(a_injector, loop):
    try:
        iv = None
        iv = await a_injector(ContainerImage, "base")
        path = iv.path
        assert os.path.exists(os.path.join(path, "bin/bash"))
    finally: del iv


