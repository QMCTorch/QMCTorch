import unittest

from qmctorch.sampler.walkers import Walkers
from .test_sampler_base import TestSamplerBase


class TestWalkers(TestSamplerBase):
    def test_walkers_init(self):
        """Test different initialization methods of the walkers."""
        _ = Walkers(
            nwalkers=10, nelec=self.mol.nelec, ndim=3, init=self.mol.domain("center")
        )

        _ = Walkers(
            nwalkers=10, nelec=self.mol.nelec, ndim=3, init=self.mol.domain("uniform")
        )

        _ = Walkers(
            nwalkers=10, nelec=self.mol.nelec, ndim=3, init=self.mol.domain("normal")
        )

        _ = Walkers(
            nwalkers=10, nelec=self.mol.nelec, ndim=3, init=self.mol.domain("atomic")
        )


if __name__ == "__main__":
    unittest.main()
