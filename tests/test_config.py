# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest, os.path
from carthage.config import *
from carthage.dependency_injection import Injector, inject
resource_dir = os.path.dirname(__file__)

def test_add_config(ainjector):
    injector = ainjector.injector
    class Defaults(ConfigSchema, prefix = ""):
        foo:int = 33
    cl = injector(ConfigLayout)
    assert cl.foo == 33
    
def test_override_config(ainjector):
    injector = ainjector.injector
    injector.replace_provider(ConfigLayout)

    @inject(cl = ConfigLayout)
    def fn(cl):
        assert cl.hadron_operations == 99
    injector.add_provider(config_key("hadron_operations"), 99)
    injector(fn)
                          

def test_substitution_in_yaml(ainjector):
    injector = ainjector.injector(Injector)
    cl = injector(ConfigLayout)
    cl.load_yaml(open(os.path.join(resource_dir, "override-config.yml"),'rt'))
    assert cl.hadron_operations == "/srv/images/test/hadron-operations"
    
