import torch
from torch import nn
from typing import Optional, List, Tuple
from .norm_orbital import atomic_orbital_norm
from .radial_functions import (
    radial_gaussian,
    radial_gaussian_pure,
    radial_slater,
    radial_slater_pure,
)
from .spherical_harmonics import Harmonics
from ...scf import Molecule


class AtomicOrbitals(nn.Module):
    def __init__(self, mol: Molecule, cuda: Optional[bool] = False) -> None:
        """Computes the value of atomic orbitals

        Args:
            mol (Molecule): Molecule object
            cuda (bool, optional): Turn GPU ON/OFF Defaults to False.
        """

        super(AtomicOrbitals, self).__init__()
        dtype = torch.get_default_dtype()

        # wavefunction data
        self.nelec = mol.nelec
        self.norb = mol.basis.nao
        self.ndim = 3

        # make the atomic position optmizable
        self.atom_coords = nn.Parameter(
            torch.as_tensor(mol.basis.atom_coords_internal).type(dtype)
        )
        self.atom_coords.requires_grad = True
        self.natoms = len(self.atom_coords)
        self.atomic_number = mol.atomic_number

        # define the BAS positions.
        self.nshells = torch.as_tensor(mol.basis.nshells)
        self.nao_per_atom = torch.as_tensor(mol.basis.nao_per_atom)
        self.bas_coords = self.atom_coords.repeat_interleave(self.nshells, dim=0)
        self.nbas = len(self.bas_coords)

        # index for the contractions
        self.index_ctr = torch.as_tensor(mol.basis.index_ctr)
        self.nctr_per_ao = torch.as_tensor(mol.basis.nctr_per_ao)
        self.contract = not len(torch.unique(self.index_ctr)) == len(self.index_ctr)

        # get the coeffs of the bas
        self.bas_coeffs = torch.as_tensor(mol.basis.bas_coeffs).type(dtype)

        # get the exponents of the bas
        self.bas_exp = nn.Parameter(torch.as_tensor(mol.basis.bas_exp).type(dtype))
        self.bas_exp.requires_grad = True

        # harmonics generator
        self.harmonics_type = mol.basis.harmonics_type
        if mol.basis.harmonics_type == "sph":
            self.bas_n = torch.as_tensor(mol.basis.bas_n).type(dtype)
            self.harmonics = Harmonics(
                mol.basis.harmonics_type,
                bas_l=mol.basis.bas_l,
                bas_m=mol.basis.bas_m,
                cuda=cuda,
            )

        elif mol.basis.harmonics_type == "cart":
            self.bas_n = torch.as_tensor(mol.basis.bas_kr).type(dtype)
            self.harmonics = Harmonics(
                mol.basis.harmonics_type,
                bas_kx=mol.basis.bas_kx,
                bas_ky=mol.basis.bas_ky,
                bas_kz=mol.basis.bas_kz,
                cuda=cuda,
            )

        # select the radial apart
        radial_dict = {
            "sto": radial_slater,
            "gto": radial_gaussian,
            "sto_pure": radial_slater_pure,
            "gto_pure": radial_gaussian_pure,
        }
        self.radial = radial_dict[mol.basis.radial_type]
        self.radial_type = mol.basis.radial_type

        # get the normalisation constants
        if hasattr(mol.basis, "bas_norm") and False:
            self.norm_cst = torch.as_tensor(mol.basis.bas_norm).type(dtype)
        else:
            with torch.no_grad():
                self.norm_cst = atomic_orbital_norm(mol.basis).type(dtype)

        # register a backflow_trans for consistency
        self.backflow_trans = None

        self.cuda = cuda
        self.device = torch.device("cpu")
        if self.cuda:
            self._to_device()

    def __repr__(self) -> None:
        name = self.__class__.__name__
        return name + "(%s, %s, %d -> (%d,%d) )" % (
            self.radial_type,
            self.harmonics_type,
            self.nelec * self.ndim,
            self.nelec,
            self.norb,
        )

    def _to_device(self) -> None:
        """Export the non parameter variable to the device."""

        self.device = torch.device("cuda")
        self.to(self.device)
        attrs = [
            "bas_n",
            "bas_coeffs",
            "nshells",
            "norm_cst",
            "index_ctr",
            "nctr_per_ao",
            "nao_per_atom",
        ]
        for at in attrs:
            self.__dict__[at] = self.__dict__[at].to(self.device)

    def forward(
        self,
        pos: torch.Tensor,
        derivative: Optional[List[int]] = [0],
        sum_grad: Optional[bool] = True,
        sum_hess: Optional[bool] = True,
        one_elec: Optional[bool] = False,
    ) -> torch.Tensor:
        """Computes the values of the atomic orbitals.

        .. math::
            \phi_i(r_j) = \sum_n c_n \\text{Rad}^{i}_n(r_j) \\text{Y}^{i}_n(r_j)

        where Rad is the radial part and Y the spherical harmonics part.
        It is also possible to compute the first and second derivatives

        .. math::
            \\nabla \phi_i(r_j) = \\frac{d}{dx_j} \phi_i(r_j) + \\frac{d}{dy_j} \phi_i(r_j) + \\frac{d}{dz_j} \phi_i(r_j) \n
            \\text{grad} \phi_i(r_j) = (\\frac{d}{dx_j} \phi_i(r_j), \\frac{d}{dy_j} \phi_i(r_j), \\frac{d}{dz_j} \phi_i(r_j)) \n
            \Delta \phi_i(r_j) = \\frac{d^2}{dx^2_j} \phi_i(r_j) + \\frac{d^2}{dy^2_j} \phi_i(r_j) + \\frac{d^2}{dz^2_j} \phi_i(r_j)

        Args:
            pos (torch.tensor): Positions of the electrons
                                  Size : Nbatch, Nelec x Ndim
            derivative (int, optional): order of the derivative (0,1,2,).
                                        Defaults to 0.
            sum_grad (bool, optional): Return the sum_grad (i.e. the sum of
                                       the derivatives) or the individual
                                       terms. Defaults to True.
                                       False only for derivative=1

            sum_hess (bool, optional): Return the sum_hess (i.e. the sum of
                                       2nd the derivatives) or the individual
                                       terms. Defaults to True.
                                       False only for derivative=1

            one_elec (bool, optional): if only one electron is in input

        Returns:
            torch.tensor: Value of the AO (or their derivatives) \n
                          size : Nbatch, Nelec, Norb (sum_grad = True) \n
                          size : Nbatch, Nelec, Norb, Ndim (sum_grad = False)

        Examples::
            >>> mol = Molecule('h2.xyz')
            >>> ao = AtomicOrbitals(mol)
            >>> pos = torch.rand(100,6)
            >>> aovals = ao(pos)
            >>> daovals = ao(pos,derivative=1)
        """

        if not isinstance(derivative, list):
            derivative = [derivative]

        if not sum_grad:
            assert 1 in derivative

        if not sum_hess:
            assert 2 in derivative

        if one_elec:
            nelec_save = self.nelec
            self.nelec = 1

        if derivative == [0]:
            ao = self._compute_ao_values(pos)

        elif derivative == [1]:
            ao = self._compute_first_derivative_ao_values(pos, sum_grad)

        elif derivative == [2]:
            ao = self._compute_second_derivative_ao_values(pos, sum_hess)

        elif derivative == [3]:
            ao = self._compute_mixed_second_derivative_ao_values(pos)

        elif derivative == [0, 1, 2]:
            ao = self._compute_all_ao_values(pos)

        else:
            raise ValueError(
                "derivative must be 0, 1, 2, 3 or [0, 1, 2, 3], got ", derivative
            )

        if one_elec:
            self.nelec = nelec_save

        return ao

    def _compute_ao_values(self, pos: torch.Tensor) -> torch.Tensor:
        """Compute the value of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, NelexNdim

        Returns:
            torch.tensor: atomic orbital values size (Nbatch, Nelec, Norb)
        """
        xyz, r = self._process_position(pos)

        R = self.radial(r, self.bas_n, self.bas_exp)

        Y = self.harmonics(xyz)
        return self._ao_kernel(R, Y)

    def _ao_kernel(self, R: torch.Tensor, Y: torch.Tensor) -> torch.Tensor:
        """Kernel for the ao values

        Args:
            R (torch.tensor): radial part of the AOs
            Y (torch.tensor): harmonics part of the AOs

        Returns:
            torch.tensor: values of the AOs (with contraction)
        """
        ao = self.norm_cst * R * Y
        if self.contract:
            ao = self._contract(ao)
        return ao

    def _compute_first_derivative_ao_values(
        self, pos: torch.Tensor, sum_grad: bool
    ) -> torch.Tensor:
        """Compute the value of the derivative of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim
            sum_grad (boolean): return the sum_grad (True) or gradient (False)

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb) if sum_grad
                          size (Nbatch, Nelec, Norb, Ndim) if sum_grad=False
        """
        if sum_grad:
            return self._compute_sum_gradient_ao_values(pos)
        else:
            return self._compute_gradient_ao_values(pos)

    def _compute_sum_gradient_ao_values(self, pos: torch.Tensor) -> torch.Tensor:
        """Compute the jacobian of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb)

        """

        xyz, r = self._process_position(pos)

        R, dR = self.radial(r, self.bas_n, self.bas_exp, xyz=xyz, derivative=[0, 1])

        Y, dY = self.harmonics(xyz, derivative=[0, 1])

        return self._sum_gradient_kernel(R, dR, Y, dY)

    def _sum_gradient_kernel(
        self, R: torch.Tensor, dR: torch.Tensor, Y: torch.Tensor, dY: torch.Tensor
    ) -> torch.Tensor:
        """Kernel for the jacobian of the ao values

        Args:
            R (torch.tensor): radial part of the AOs
            dR (torch.tensor): derivative of the radial part of the AOs
            Y (torch.tensor): harmonics part of the AOs
            dY (torch.tensor): derivative of the harmonics part of the AOs

        Returns:
            torch.tensor: values of the jacobian of the AOs (with contraction)
        """
        dao = self.norm_cst * (dR * Y + R * dY)
        if self.contract:
            dao = self._contract(dao)
        return dao

    def _compute_gradient_ao_values(self, pos: torch.Tensor) -> torch.Tensor:
        """Compute the gradient of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb, Ndim)

        """
        xyz, r = self._process_position(pos)

        R, dR = self.radial(
            r, self.bas_n, self.bas_exp, xyz=xyz, derivative=[0, 1], sum_grad=False
        )

        Y, dY = self.harmonics(xyz, derivative=[0, 1], sum_grad=False)

        return self._gradient_kernel(R, dR, Y, dY)

    def _gradient_kernel(
        self, R: torch.Tensor, dR: torch.Tensor, Y: torch.Tensor, dY: torch.Tensor
    ) -> torch.Tensor:
        """Kernel for the gradient of the ao values

        Args:
            R (torch.tensor): radial part of the AOs
            dR (torch.tensor): derivative of the radial part of the AOs
            Y (torch.tensor): harmonics part of the AOs
            dY (torch.tensor): derivative of the harmonics part of the AOs

        Returns:
            torch.tensor: values of the gradient of the AOs (with contraction)
        """
        nbatch = R.shape[0]
        bas = dR * Y.unsqueeze(-1) + R.unsqueeze(-1) * dY

        bas = self.norm_cst.unsqueeze(-1) * self.bas_coeffs.unsqueeze(-1) * bas

        if self.contract:
            ao = torch.zeros(nbatch, self.nelec, self.norb, 3, device=self.device).type(
                torch.get_default_dtype()
            )
            ao.index_add_(2, self.index_ctr, bas)
        else:
            ao = bas
        return ao

    def _compute_second_derivative_ao_values(
        self, pos: torch.Tensor, sum_hess: bool
    ) -> torch.Tensor:
        """Compute the values of the 2nd derivative of the ao from the xyz and r tensors

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim
            sum_hess (boolean): return the sum_hess (True) or gradient (False)

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb) if sum_hess
                          size (Nbatch, Nelec, Norb, Ndim) if sum_hess=False
        """
        if sum_hess:
            return self._compute_sum_diag_hessian_ao_values(pos)
        else:
            return self._compute_diag_hessian_ao_values(pos)

    def _compute_sum_diag_hessian_ao_values(self, pos: torch.Tensor) -> torch.Tensor:
        """Compute the laplacian of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb)

        """
        xyz, r = self._process_position(pos)

        R, dR, d2R = self.radial(
            r, self.bas_n, self.bas_exp, xyz=xyz, derivative=[0, 1, 2], sum_grad=False
        )

        Y, dY, d2Y = self.harmonics(xyz, derivative=[0, 1, 2], sum_grad=False)
        return self._sum_diag_hessian_kernel(R, dR, d2R, Y, dY, d2Y)

    def _sum_diag_hessian_kernel(
        self,
        R: torch.Tensor,
        dR: torch.Tensor,
        d2R: torch.Tensor,
        Y: torch.Tensor,
        dY: torch.Tensor,
        d2Y: torch.Tensor,
    ) -> torch.Tensor:
        """Kernel for the sum of the diag hessian of the ao values

        Args:
            R (torch.tensor): radial part of the AOs
            dR (torch.tensor): derivative of the radial part of the AOs
            d2R (torch.tensor): 2nd derivative of the radial part of the AOs
            Y (torch.tensor): harmonics part of the AOs
            dY (torch.tensor): derivative of the harmonics part of the AOs
            d2Y (torch.tensor): 2nd derivative of the harmonics part of the AOs

        Returns:
            torch.tensor: values of the laplacian of the AOs (with contraction)
        """

        d2ao = self.norm_cst * (d2R * Y + 2.0 * (dR * dY).sum(3) + R * d2Y)
        if self.contract:
            d2ao = self._contract(d2ao)
        return d2ao

    def _compute_diag_hessian_ao_values(self, pos: torch.Tensor) -> torch.Tensor:
        """Compute the individual elements of the laplacian of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb, 3)

        """

        xyz, r = self._process_position(pos)

        R, dR, d2R = self.radial(
            r,
            self.bas_n,
            self.bas_exp,
            xyz=xyz,
            derivative=[0, 1, 2],
            sum_grad=False,
            sum_hess=False,
        )

        Y, dY, d2Y = self.harmonics(
            xyz, derivative=[0, 1, 2], sum_grad=False, sum_hess=False
        )

        return self._diag_hessian_kernel(R, dR, d2R, Y, dY, d2Y)

    def _diag_hessian_kernel(
        self,
        R: torch.Tensor,
        dR: torch.Tensor,
        d2R: torch.Tensor,
        Y: torch.Tensor,
        dY: torch.Tensor,
        d2Y: torch.Tensor,
    ) -> torch.Tensor:
        """Kernel for the diagonal hessian of the ao values

        Args:
            R (torch.tensor): radial part of the AOs
            dR (torch.tensor): derivative of the radial part of the AOs
            d2R (torch.tensor): 2nd derivative of the radial part of the AOs
            Y (torch.tensor): harmonics part of the AOs
            dY (torch.tensor): derivative of the harmonics part of the AOs
            d2Y (torch.tensor): 2nd derivative of the harmonics part of the AOs

        Returns:
            torch.tensor: values of the laplacian of the AOs (with contraction)
        """

        nbatch = R.shape[0]

        bas = (
            self.norm_cst.unsqueeze(-1)
            * self.bas_coeffs.unsqueeze(-1)
            * (d2R * Y.unsqueeze(-1) + 2.0 * (dR * dY) + R.unsqueeze(-1) * d2Y)
        )

        if self.contract:
            d2ao = torch.zeros(
                nbatch, self.nelec, self.norb, 3, device=self.device
            ).type(torch.get_default_dtype())
            d2ao.index_add_(2, self.index_ctr, bas)
        else:
            d2ao = bas

        return d2ao

    def _compute_mixed_second_derivative_ao_values(
        self, pos: torch.Tensor
    ) -> torch.Tensor:
        """Compute the mixed second derivative of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            torch.tensor: derivative of atomic orbital values
                          size (Nbatch, Nelec, Norb)

        """
        xyz, r = self._process_position(pos)

        R, dR, d2R, d2mR = self.radial(
            r,
            self.bas_n,
            self.bas_exp,
            xyz=xyz,
            derivative=[0, 1, 2, 3],
            sum_grad=False,
        )

        Y, dY, d2Y, d2mY = self.harmonics(xyz, derivative=[0, 1, 2, 3], sum_grad=False)

        return self._off_diag_hessian_kernel(R, dR, d2R, d2mR, Y, dY, d2Y, d2mY)

    def _off_diag_hessian_kernel(
        self,
        R: torch.Tensor,
        dR: torch.Tensor,
        d2R: torch.Tensor,
        d2mR: torch.Tensor,
        Y: torch.Tensor,
        dY: torch.Tensor,
        d2Y: torch.Tensor,
        d2mY: torch.Tensor,
    ) -> torch.Tensor:
        """Kernel for the off diagonal hessian of the ao values

        Args:
            R (torch.Tensor): radial part of the AOs
            dR (torch.Tensor): derivative of the radial part of the AOs
            d2R (torch.Tensor): 2nd derivative of the radial part of the AOs
            d2mR (torch.Tensor): mixed 2nd derivative of the radial part of the AOs
            Y (torch.Tensor): harmonics part of the AOs
            dY (torch.Tensor): derivative of the harmonics part of the AOs
            d2Y (torch.Tensor): 2nd derivative of the harmonics part of the AOs
            d2mY (torch.Tensor): 2nd mixed derivative of the harmonics part of the AOs

        Returns:
            torch.Tensor: values of the mixed derivative of the AOs (with contraction)
        """

        nbatch = R.shape[0]

        bas = (
            self.norm_cst.unsqueeze(-1)
            * self.bas_coeffs.unsqueeze(-1)
            * (
                d2mR * Y.unsqueeze(-1)
                + (
                    (
                        dR[..., [[0, 1], [0, 2], [1, 2]]]
                        * dY[..., [[1, 0], [2, 0], [2, 1]]]
                    ).sum(-1)
                )
                + R.unsqueeze(-1) * d2mY
            )
        )

        if self.contract:
            d2ao = torch.zeros(
                nbatch, self.nelec, self.norb, 3, device=self.device
            ).type(torch.get_default_dtype())
            d2ao.index_add_(2, self.index_ctr, bas)
        else:
            d2ao = bas

        return d2ao

    def _compute_all_ao_values(
        self, pos: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Compute the ao, gradient, laplacian of the ao from the xyx and r tensor

        Args:
            pos (torch.tensor): position of each elec size Nbatch, Nelec x Ndim

        Returns:
            tuple(): (ao, grad and lapalcian) of atomic orbital values
                     ao size (Nbatch, Nelec, Norb)
                     dao size (Nbatch, Nelec, Norb, Ndim)
                     d2ao size (Nbatch, Nelec, Norb)
        """

        xyz, r = self._process_position(pos)

        # the gradients elements are needed to compute the second der
        # we therefore use sum_grad=False regardless of the input arg
        R, dR, d2R = self.radial(
            r, self.bas_n, self.bas_exp, xyz=xyz, derivative=[0, 1, 2], sum_grad=False
        )

        # the gradients elements are needed to compute the second der
        # we therefore use sum_grad=False regardless of the input arg
        Y, dY, d2Y = self.harmonics(xyz, derivative=[0, 1, 2], sum_grad=False)

        return (
            self._ao_kernel(R, Y),
            self._gradient_kernel(R, dR, Y, dY),
            self._sum_diag_hessian_kernel(R, dR, d2R, Y, dY, d2Y),
        )

    def _process_position(self, pos: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Computes the positions/distance bewteen elec/orb

        Args:
            pos (torch.tensor): positions of the walkers Nbat, NelecxNdim

        Returns:
            torch.tensor, torch.tensor: positions of the elec wrt the bas
                                        (Nbatch, Nelec, Norb, Ndim)
                                        distance between elec and bas
                                        (Nbatch, Nelec, Norb)
        """
        # get the elec-atom vectors/distances
        xyz, r = self._elec_atom_dist(pos)

        # repeat/interleave to get vector and distance between
        # electrons and orbitals
        return (
            xyz.repeat_interleave(self.nshells, dim=2),
            r.repeat_interleave(self.nshells, dim=2),
        )

    def _elec_atom_dist(self, pos: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Computes the positions/distance bewteen elec/atoms

        Args:
            pos (torch.tensor): positions of the walkers : Nbatch x [Nelec*Ndim]

        Returns:
            (torch.tensor, torch.tensor): positions of the elec wrt the atoms
                                        [Nbatch x Nelec x Natom x Ndim]
                                        distance between elec and atoms
                                        [Nbatch x Nelec x Natom]
        """

        # compute the vectors between electrons and atoms
        xyz = pos.view(-1, self.nelec, 1, self.ndim) - self.atom_coords[None, ...]

        # distance between electrons and atoms
        r = torch.sqrt((xyz * xyz).sum(3))

        return xyz, r

    def _contract(self, bas: torch.Tensor) -> torch.Tensor:
        """Contrat the basis set to form the atomic orbitals

        Args:
            bas (torch.tensor): values of the basis function

        Returns:
            torch.tensor: values of the contraction
        """
        nbatch = bas.shape[0]
        bas = self.bas_coeffs * bas
        cbas = torch.zeros(nbatch, self.nelec, self.norb, device=self.device).type(
            torch.get_default_dtype()
        )
        cbas.index_add_(2, self.index_ctr, bas)
        return cbas

    def update(self, ao: torch.Tensor, pos: torch.Tensor, idelec: int) -> torch.Tensor:
        """Update an AO matrix with the new positions of one electron

        Args:
            ao (torch.tensor): initial AO matrix
            pos (torch.tensor): new positions of some electrons
            idelec (int): index of the electron that has moved

        Returns:
            torch.tensor: new AO matrix

        Examples::
            >>> mol = Molecule('h2.xyz')
            >>> ao = AtomicOrbitals(mol)
            >>> pos = torch.rand(100,6)
            >>> aovals = ao(pos)
            >>> id = 0
            >>> pos[:,:3] = torch.rand(100,3)
            >>> ao.update(aovals, pos, 0)
        """

        ao_new = ao.clone()
        ids, ide = (idelec) * 3, (idelec + 1) * 3
        ao_new[:, idelec, :] = self.forward(pos[:, ids:ide], one_elec=True).squeeze(1)
        return ao_new
