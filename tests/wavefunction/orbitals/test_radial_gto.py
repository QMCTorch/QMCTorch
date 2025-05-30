import unittest

import numpy as np
import torch

from qmctorch.scf import Molecule
from qmctorch.wavefunction.orbitals.atomic_orbitals import AtomicOrbitals
from .second_derivative import second_derivative


class TestRadialSlater(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(0)
        np.random.seed(0)

        self.mol = Molecule(
            atom="C 0 0 0; O 0 0 2.190; O 0 0 -2.190",
            calculator="pyscf",
            basis="dzp",
            unit="bohr",
        )

        # wave function
        self.ao = AtomicOrbitals(self.mol)

    def test_first_derivative_x(self):
        npts = 1000
        self.pos = torch.zeros(npts, self.mol.nelec * 3)
        self.pos[:, 0] = torch.linspace(-4, 4, npts)
        self.dx = self.pos[1, 0] - self.pos[0, 0]

        xyz, r = self.ao._process_position(self.pos)
        R, dR = self.ao.radial(
            r,
            self.ao.bas_n,
            self.ao.bas_exp,
            xyz=xyz,
            derivative=[0, 1],
            sum_grad=False,
        )

        R = R.detach().numpy()
        dR = dR.detach().numpy()
        ielec = 0

        for iorb in range(7):
            r0 = R[:, ielec, iorb]
            dz_r0 = dR[:, ielec, iorb, 0]
            dz_r0_fd = np.gradient(r0, self.dx)
            delta = np.delete(np.abs(dz_r0 - dz_r0_fd), np.s_[450:550])

            # plt.plot(dz_r0)
            # plt.plot(dz_r0_fd)
            # plt.show()

            assert np.all(delta < 1e-3)

    def test_first_derivative_y(self):
        npts = 1000
        self.pos = torch.zeros(npts, self.mol.nelec * 3)
        self.pos[:, 1] = torch.linspace(-4, 4, npts)
        self.dy = self.pos[1, 1] - self.pos[0, 1]

        xyz, r = self.ao._process_position(self.pos)
        R, dR = self.ao.radial(
            r,
            self.ao.bas_n,
            self.ao.bas_exp,
            xyz=xyz,
            derivative=[0, 1],
            sum_grad=False,
        )

        R = R.detach().numpy()
        dR = dR.detach().numpy()
        ielec = 0

        for iorb in range(7):
            r0 = R[:, ielec, iorb]
            dz_r0 = dR[:, ielec, iorb, 1]
            dz_r0_fd = np.gradient(r0, self.dy)
            delta = np.delete(np.abs(dz_r0 - dz_r0_fd), np.s_[450:550])

            # plt.plot(dz_r0)
            # plt.plot(dz_r0_fd)
            # plt.show()

            assert np.all(delta < 1e-3)

    def test_first_derivative_z(self):
        npts = 1000
        self.pos = torch.zeros(npts, self.mol.nelec * 3)
        self.pos[:, 2] = torch.linspace(-4, 4, npts)
        self.dz = self.pos[1, 2] - self.pos[0, 2]

        xyz, r = self.ao._process_position(self.pos)
        R, dR = self.ao.radial(
            r,
            self.ao.bas_n,
            self.ao.bas_exp,
            xyz=xyz,
            derivative=[0, 1],
            sum_grad=False,
        )
        R = R.detach().numpy()
        dR = dR.detach().numpy()
        ielec = 0

        for iorb in range(7):
            r0 = R[:, ielec, iorb]
            dz_r0 = dR[:, ielec, iorb, 2]
            dz_r0_fd = np.gradient(r0, self.dz)
            delta = np.delete(np.abs(dz_r0 - dz_r0_fd), np.s_[450:550])

            # plt.plot(dz_r0)
            # plt.plot(dz_r0_fd)
            # plt.show()

            assert np.all(delta < 1e-3)

    def test_laplacian(self, eps=1e-4):
        npts = 1000

        z = torch.linspace(-3, 3, npts)
        self.pos = torch.zeros(npts, self.mol.nelec * 3)
        self.pos[:, 2] = z
        eps = self.pos[1, 2] - self.pos[0, 2]

        self.pos[:, 3] = eps
        self.pos[:, 5] = z

        self.pos[:, 6] = -eps
        self.pos[:, 8] = z

        self.pos[:, 10] = eps
        self.pos[:, 11] = z

        self.pos[:, 13] = -eps
        self.pos[:, 14] = z

        xyz, r = self.ao._process_position(self.pos)
        R, _, d2R = self.ao.radial(
            r,
            self.ao.bas_n,
            self.ao.bas_exp,
            xyz=xyz,
            derivative=[0, 1, 2],
            sum_grad=False,
        )

        for iorb in range(7):
            lap_analytic = np.zeros(npts - 2)
            lap_fd = np.zeros(npts - 2)

            for i in range(1, npts - 1):
                lap_analytic[i - 1] = d2R[i, 0, iorb]

                r0 = R[i, 0, iorb].detach().numpy()
                rpz = R[i + 1, 0, iorb].detach().numpy()
                rmz = R[i - 1, 0, iorb].detach().numpy()
                d2z = second_derivative(rmz, r0, rpz, eps)

                r0 = R[i, 0, iorb]
                rpx = R[i, 1, iorb]
                rmx = R[i, 2, iorb]
                d2x = second_derivative(rmx, r0, rpx, eps)

                r0 = R[i, 0, iorb]
                rpy = R[i, 3, iorb]
                rmy = R[i, 4, iorb]
                d2y = second_derivative(rmy, r0, rpy, eps)

                lap_fd[i - 1] = d2x + d2y + d2z

            m = np.abs(lap_analytic).max()
            delta = np.delete(np.abs(lap_analytic - lap_fd) / m, np.s_[450:550])

            assert np.all(delta < 5e-3)
            # plt.plot(lap_analytic, linewidth=2)
            # plt.plot(lap_fd)
            # plt.show()


if __name__ == "__main__":
    unittest.main()

    # t = TestRadialSlater()
    # t.setUp()
    # # t.test_first_derivative_x()
    # # t.test_first_derivative_y()
    # # t.test_first_derivative_z()
    # t.test_laplacian()
