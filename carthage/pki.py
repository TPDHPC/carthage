import os, os.path
from .dependency_injection import *
from . import sh
from .config import ConfigLayout
from .utils import memoproperty

@inject(config_layout = ConfigLayout)
class PkiManager(Injectable):

    def __init__(self, config_layout):
        self.config_layout = config_layout
        os.makedirs(self.pki_dir, exist_ok = True)

    def credentials(self, host):
        "Returns a key combined with certificate"
        self._certify(host)
        s = ""
        for ext in ('pem', 'key'):
            with open(os.path.join(self.pki_dir, "{}.{}".format(host, ext))) as f:
                s += f.read()
        return s

    def _certify(self, host):
        self.ca_cert
        sh.entanglement_pki(host, d=self.pki_dir)

    @memoproperty
    def pki_dir(self):
        return os.path.join(self.config_layout.state_dir, "pki")

    @property
    def ca_cert(self):
        sh.entanglement_pki('-d', self.pki_dir,
                            '--ca-name', "Carthage Root CA")
        with open(self.pki_dir+'/ca.pem','rt') as f:
            return f.read()
        
