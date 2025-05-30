import os
import shutil
import warnings
from types import SimpleNamespace
from typing import BinaryIO, List
import numpy as np

from ... import log
from ...utils.constants import BOHR2ANGS
from .calculator_base import CalculatorBase

try:
    from scm import plams
except ModuleNotFoundError:
    warnings.warn("scm python module not found")


class CalculatorADF(CalculatorBase):
    def __init__(  # pylint: disable=too-many-arguments
        self,
        atoms: list,
        atom_coords: list,
        basis: str,
        charge: int,
        spin: int,
        scf: str,
        units: str,
        molname: str,
        savefile: str,
    ) -> None:
        """
        Initialize the ADF calculator.

        Args:
            atoms (list): List of atom symbols.
            atom_coords (list): List of atomic coordinates.
            basis (str): Basis set name.
            charge (int): Molecular charge.
            spin (int): Spin multiplicity.
            scf (str): Self-consistent field method.
            units (str): Units for atomic coordinates.
            molname (str): Molecule name.
            savefile (str): File name to save results.

        Raises:
            ValueError: If charge or spin is not supported.

        Returns:
            None
        """
        CalculatorBase.__init__(
            self,
            atoms,
            atom_coords,
            basis,
            charge,
            spin,
            scf,
            units,
            molname,
            "adf",
            savefile,
        )

        if charge != 0:
            raise ValueError(
                "ADF calculator does not support charge yet, open an issue in the repo :)"
            )

        if spin != 0:
            raise ValueError(
                "ADF calculator does not support spin polarization yet, open an issue in the repo :)"
            )

        # basis from the emma paper
        self.additional_basis_type = ["VB1", "VB2", "VB3", "CVB1", "CVB2", "CVB3"]

        self.additional_basis_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "atomicdata/adf/"
        )

        self.adf_version = "adf2020+"
        self.job_name = "".join(self.atoms) + "_" + self.basis_name
        self.output_file = "adf.rkf"

    def run(self) -> SimpleNamespace:
        """Run the calculation using ADF."""

        # path needed for the calculation
        plams_wd = "./plams_workdir"
        outputdir_path = os.path.join(
            plams_wd, os.path.join(self.job_name, self.output_file)
        )

        # get the correct exec
        plams_job = {"adf2020+": plams.AMSJob, "adf2019": plams.ADFJob}[
            self.adf_version
        ]

        # configure plams and run the calculation
        self.init_plams()
        mol = self.get_plams_molecule()
        sett = self.get_plams_settings()
        job = plams_job(molecule=mol, settings=sett, name=self.job_name)
        job.run()

        # extract the data to hdf5
        basis = self.get_basis_data(outputdir_path)

        # remove adf data
        if self.savefile:
            shutil.copyfile(outputdir_path, self.output_file)
            self.savefile = self.output_file
        # shutil.rmtree(plams_wd)

        # fnalize plams
        self.finish_plams()

        return basis

    def init_plams(self) -> None:
        """Init PLAMS."""
        plams.init()
        plams.config.log.stdout = -1
        plams.config.log.file = -1
        plams.config.erase_workdir = True

    def finish_plams(self) -> None:
        """Finish PLAMS."""
        plams.finish()

    def get_plams_molecule(self) -> plams.Molecule:
        """Returns a plams molecule object."""
        mol = plams.Molecule()
        bohr2angs = BOHR2ANGS  # the coordinate are always in bohr
        for at, xyz in zip(self.atoms, self.atom_coords):
            xyz = list(bohr2angs * np.array(xyz))
            mol.add_atom(plams.Atom(symbol=at, coords=tuple(xyz)))
        return mol

    def get_plams_settings(self) -> plams.Settings:
        """
        Returns a plams setting object.

        Returns:
            plams.Settings: A plams setting object.
        """

        sett = plams.Settings()
        sett.input.ams.Task = "SinglePoint"

        if self.basis_name.upper() in self.additional_basis_type:
            sett.input.adf.basis.type = "DZP"
            parsed_atoms = []
            for at in self.atoms:
                if at not in parsed_atoms:
                    basis_path = os.path.join(
                        self.additional_basis_path, self.basis_name.upper(), at
                    )
                    atomtype = f"Symbol={at} File={basis_path}"
                    sett.input.adf.basis.peratomtype = atomtype
                    parsed_atoms.append(at)
        else:
            sett.input.adf.basis.type = self.basis_name.upper()

        sett.input.adf.basis.core = "None"
        sett.input.adf.symmetry = "nosym"

        if self.scf.lower() == "hf":
            sett.input.adf.XC.HartreeFock = ""

        elif self.scf.lower() == "dft":
            sett.input.adf.XC.LDA = "VWN"

        sett.input.adf.relativity.level = "None"

        # total energy
        sett.input.adf.totalenergy = True

        # charge info
        # sett.input.adf.charge = "%d %d" % (self.charge, self.spin)

        # spin info
        sett.input.unrestricted = False
        # sett.input.spinpolarization = self.spin

        return sett

    def get_basis_data(self, kffile: str) -> SimpleNamespace:
        """
        Save the basis information needed to compute the AO values.

        Args:
            kffile (str): Path to the KF file.

        Returns:
            SimpleNamespace: A namespace containing the basis information.
        """
        if not os.path.isfile(kffile):
            raise FileNotFoundError(
                "File %s not found, ADF may have crashed, look into the plams_workdir directory"
                % kffile
            )
        kf = plams.KFFile(kffile)
        status = kf.read("General", "termination status").strip()
        if status != "NORMAL TERMINATION":
            log.info("  WARNING : ADF calculation terminated with status")
            log.info("          : %s" % status)
            log.info("          : Proceed with caution")

        basis = SimpleNamespace()

        basis.TotalEnergy = kf.read("Total Energy", "Total energy")
        basis.radial_type = "sto"
        basis.harmonics_type = "cart"

        nao = kf.read("Basis", "naos")
        nmo = kf.read("A", "nmo_A")
        basis.nao = nao
        basis.nmo = nmo

        # number of bas per atom type
        nbptr = kf.read("Basis", "nbptr")

        # number of atom per atom typ
        nqptr = kf.read("Geometry", "nqptr")
        atom_type = kf.read("Geometry", "atomtype").split()

        # number of bas per atom type
        nshells = np.array([nbptr[i] - nbptr[i - 1] for i in range(1, len(nbptr))])

        # kx/ky/kz/kr exponent per atom type
        bas_kx = self.read_array(kf, "Basis", "kx")
        bas_ky = self.read_array(kf, "Basis", "ky")
        bas_kz = self.read_array(kf, "Basis", "kz")
        bas_kr = self.read_array(kf, "Basis", "kr")

        # bas exp/coeff/norm per atom type
        bas_exp = self.read_array(kf, "Basis", "alf")
        bas_norm = self.read_array(kf, "Basis", "bnorm")

        basis_nshells = []
        basis_bas_kx, basis_bas_ky, basis_bas_kz = [], [], []
        basis_bas_kr = []
        basis_bas_exp, basis_bas_norm = [], []

        for iat, _ in enumerate(atom_type):
            number_copy = nqptr[iat + 1] - nqptr[iat]
            idx_bos = list(range(nbptr[iat] - 1, nbptr[iat + 1] - 1))

            basis_nshells += [nshells[iat]] * number_copy

            basis_bas_kx += list(bas_kx[idx_bos]) * number_copy
            basis_bas_ky += list(bas_ky[idx_bos]) * number_copy
            basis_bas_kz += list(bas_kz[idx_bos]) * number_copy
            basis_bas_kr += list(bas_kr[idx_bos]) * number_copy
            basis_bas_exp += list(bas_exp[idx_bos]) * number_copy
            basis_bas_norm += list(bas_norm[idx_bos]) * number_copy

        basis.nshells = basis_nshells
        basis.nao_per_atom = basis_nshells
        basis.index_ctr = np.arange(nao)
        basis.nctr_per_ao = np.ones(nao)

        basis.bas_kx = np.array(basis_bas_kx)
        basis.bas_ky = np.array(basis_bas_ky)
        basis.bas_kz = np.array(basis_bas_kz)
        basis.bas_kr = np.array(basis_bas_kr)

        basis.bas_exp = np.array(basis_bas_exp)
        basis.bas_coeffs = np.ones_like(basis_bas_exp)
        basis.bas_norm = np.array(basis_bas_norm)

        basis.atom_coords_internal = np.array(kf.read("Geometry", "xyz")).reshape(-1, 3)

        # Molecular orbitals
        mos = np.array(kf.read("A", "Eigen-Bas_A"))
        mos = mos.reshape(nmo, nao).T

        # normalize the MO
        # this is not needed !!!
        # mos = self.normalize_columns(mos)

        # orbital that take part in the rep
        npart = np.array(kf.read("A", "npart")) - 1

        # create permutation matrix
        perm_mat = np.zeros((basis.nao, basis.nao))
        for i in range(basis.nao):
            perm_mat[npart[i], i] = 1.0

        # reorder the basis function
        basis.mos = perm_mat @ mos

        return basis

    @staticmethod
    def read_array(kf: BinaryIO, section: str, name: str) -> np.ndarray:
        """read a data from the kf file

        Args:
            kf (BinaryIO): kf file
            section (str): name of the section
            name (str): name of the property

        Returns:
            np.ndarray: data
        """
        data = np.array(kf.read(section, name))
        if data.shape == ():
            data = np.array([data])
        return data


class CalculatorADF2019(CalculatorADF):
    def __init__(
        self,
        atoms: List[str],
        atom_coords: List[np.ndarray],
        basis: str,
        charge: int,
        spin: int,
        scf: str,
        units: str,
        molname: str,
        savefile: str,
    ):
        """
        Initialize the ADF2019 calculator.

        Args:
            atoms (list): List of atom symbols.
            atom_coords (list): List of atomic coordinates.
            basis (str): Basis set name.
            charge (int): Molecular charge.
            spin (int): Spin multiplicity.
            scf (str): Self-consistent field method.
            units (str): Units of the coordinates; 'bohr' or 'angs'.
            molname (str): Molecule name.
            savefile (str): File name to save results.

        Returns:
            None
        """
        CalculatorADF.__init__(
            self, atoms, atom_coords, basis, charge, spin, scf, units, molname, savefile
        )

        self.adf_version = "adf2019"
        self.job_name = "".join(self.atoms) + "_" + self.basis_name
        self.output_file = self.job_name + ".t21"

    def get_plams_molecule(self) -> plams.Molecule:
        """Returns a plams molecule object.

        Returns:
            plams.Molecule: A plams molecule object.
        """
        mol = plams.Molecule()
        for at, xyz in zip(self.atoms, self.atom_coords):
            mol.add_atom(plams.Atom(symbol=at, coords=tuple(xyz)))
        return mol

    def get_plams_settings(self) -> plams.Settings:
        """Returns a plams setting object.

        Returns:
            plams.Settings: A plams setting object.
        """

        sett = plams.Settings()
        sett.input.basis.type = self.basis_name.upper()
        if self.basis_name.upper() in self.additional_basis_type:
            sett.input.basis.path = self.additional_basis_path
        sett.input.basis.core = "None"
        sett.input.symmetry = "nosym"

        if self.scf.lower() == "hf":
            sett.input.XC.HartreeFock = ""

        elif self.scf.lower() == "dft":
            sett.input.XC.LDA = "VWN"

        # correct unit
        if self.units == "angs":
            sett.input.units.length = "Angstrom"
        elif self.units == "bohr":
            sett.input.units.length = "Bohr"

        # total energy
        sett.input.totalenergy = True

        # charge info
        sett.input.charge = "%d %d" % (self.charge, self.spin)

        return sett
