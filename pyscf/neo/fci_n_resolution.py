# N resolution method for multicomponent FCI
from functools import reduce
import numpy
import scipy
from pyscf import lib
from pyscf import ao2mo
from pyscf.fci import cistring
from pyscf import neo
import time

def contract(h1, h2, fcivec, norb, nparticle, link_index=None):
    ndim = len(norb)
    if link_index is None:
        link_index = []
        for i in range(ndim):
            link_index.append(cistring.gen_linkstr_index(range(norb[i]), nparticle[i]))
    dim = []
    for i in range(ndim):
        dim.append(cistring.num_strings(norb[i], nparticle[i]))
    ci0 = fcivec.reshape(dim)

    t1 = []
    for k in range(ndim):
        t1_this = numpy.zeros((norb[k],norb[k])+tuple(dim), dtype=fcivec.dtype)
        str0_indices = [slice(None)] * ndim
        str1_indices = [slice(None)] * ndim
        for str0, tab in enumerate(link_index[k]):
            str0_indices[k] = str0
            str0_indices_tuple = tuple(str0_indices)
            for a, i, str1, sign in tab:
                str1_indices[k] = str1
                t1_this[tuple([a,i]+str1_indices)] += sign * ci0[str0_indices_tuple]
        t1.append(t1_this)

    norb_e = norb[0]
    h2e_aa = ao2mo.restore(1, h2[0][0], norb_e)
    h2e_ab = ao2mo.restore(1, h2[0][1], norb_e)
    h2e_bb = ao2mo.restore(1, h2[1][1], norb_e)

    g1 = lib.einsum('bjai,aiA->bjA', h2e_aa.reshape([norb_e]*4),
                    t1[0].reshape([norb_e]*2+[-1])) \
       + lib.einsum('bjai,aiA->bjA', h2e_ab.reshape([norb_e]*4),
                    t1[1].reshape([norb_e]*2+[-1]))
    g1 = g1.reshape([norb_e]*2+dim)

    fcinew = numpy.zeros_like(ci0, dtype=fcivec.dtype)
    for str0, tab in enumerate(link_index[0]):
        for a, i, str1, sign in tab:
            fcinew[str1] += sign * g1[a,i,str0]

    g1 = lib.einsum('bjai,aiA->bjA', h2e_bb.reshape([norb_e]*4),
                    t1[1].reshape([norb_e]*2+[-1])) \
       + lib.einsum('aibj,aiA->bjA', h2e_ab.reshape([norb_e]*4),
                    t1[0].reshape([norb_e]*2+[-1]))
    g1 = g1.reshape([norb_e]*2+dim)

    for str0, tab in enumerate(link_index[1]):
        for a, i, str1, sign in tab:
            fcinew[:,str1] += sign * g1[a,i,:,str0]

    for i in range(2, ndim):
        fcinew += numpy.dot(h1[i].reshape(-1), t1[i].reshape(-1,fcivec.size)).reshape(fcinew.shape)

    done = [[False] * ndim for _ in range(ndim)]
    for k in range(ndim):
        for l in range(ndim):
            if k != l and (k >= 2 or l >= 2) and h2[k][l] is not None and not done[k][l]:
                if k < l:
                    g1 = lib.einsum('aibj,aiA->bjA', h2[k][l].reshape([norb[k]]*2+[norb[l]]*2),
                                    t1[k].reshape([norb[k]]*2+[-1]))
                    g1 = g1.reshape([norb[l]]*2+dim)
                    str0_indices = [slice(None)] * ndim
                    str1_indices = [slice(None)] * ndim
                    for str0, tab in enumerate(link_index[l]):
                        str0_indices[l] = str0
                        for a, i, str1, sign in tab:
                            str1_indices[l] = str1
                            fcinew[tuple(str1_indices)] += sign * g1[tuple([a,i]+str0_indices)]
                else:
                    g1 = lib.einsum('bjai,aiA->bjA', h2[k][l].reshape([norb[k]]*2+[norb[l]]*2),
                                    t1[l].reshape([norb[l]]*2+[-1]))
                    g1 = g1.reshape([norb[k]]*2+dim)
                    str0_indices = [slice(None)] * ndim
                    str1_indices = [slice(None)] * ndim
                    for str0, tab in enumerate(link_index[k]):
                        str0_indices[k] = str0
                        for a, i, str1, sign in tab:
                            str1_indices[k] = str1
                            fcinew[tuple(str1_indices)] += sign * g1[tuple([a,i]+str0_indices)]
                done[k][l] = done[l][k] = True
    return fcinew.reshape(fcivec.shape)


def absorb_h1e(h1e, eri, norb, nelec, fac=1):
    '''Modify 2e Hamiltonian to include 1e Hamiltonian contribution.
    '''
    if not isinstance(nelec, (int, numpy.integer)):
        nelec = sum(nelec)
    h1e_a, h1e_b = h1e
    h2e_aa = ao2mo.restore(1, eri[0].copy(), norb).astype(h1e_a.dtype, copy=False)
    h2e_ab = ao2mo.restore(1, eri[1].copy(), norb).astype(h1e_a.dtype, copy=False)
    h2e_bb = ao2mo.restore(1, eri[2].copy(), norb).astype(h1e_a.dtype, copy=False)
    f1e_a = h1e_a - numpy.einsum('jiik->jk', h2e_aa) * .5
    f1e_b = h1e_b - numpy.einsum('jiik->jk', h2e_bb) * .5
    f1e_a *= 1./(nelec+1e-100)
    f1e_b *= 1./(nelec+1e-100)
    for k in range(norb):
        h2e_aa[:,:,k,k] += f1e_a
        h2e_aa[k,k,:,:] += f1e_a
        h2e_ab[:,:,k,k] += f1e_a
        h2e_ab[k,k,:,:] += f1e_b
        h2e_bb[:,:,k,k] += f1e_b
        h2e_bb[k,k,:,:] += f1e_b
    return (h2e_aa * fac, h2e_ab * fac, h2e_bb * fac)

def make_hdiag(h1, g2, norb, nparticle):
    t0 = time.time()
    ndim = len(norb)
    dim = []
    for i in range(ndim):
        dim.append(cistring.num_strings(norb[i], nparticle[i]))
    hdiag = numpy.zeros(tuple(dim))
    occslists = []
    for i in range(ndim):
        occslists.append(cistring.gen_occslst(range(norb[i]), nparticle[i]))
    jdiag = [[None] * ndim for _ in range(ndim)]
    kdiag = [None] * ndim
    done = [[False] * ndim for _ in range(ndim)]
    if g2 is not None:
        for k in range(ndim):
            for l in range(ndim):
                if g2[k][l] is not None and not done[k][l]:
                    if k == l:
                        g2_ = ao2mo.restore(1, g2[k][l], norb[k])
                        jdiag[k][l] = numpy.einsum('iijj->ij', g2_)
                        kdiag[k] = numpy.einsum('ijji->ij', g2_)
                    else:
                        jdiag[k][l] = numpy.einsum('iijj->ij', g2[k][l].reshape([norb[k]]*2+[norb[l]]*2))
                    done[k][l] = done[l][k] = True

    for i in range(ndim):
        if h1[i] is not None or jdiag[i][i] is not None or kdiag[i] is not None:
            str0_indices = [slice(None)] * ndim
            for str0, occ in enumerate(occslists[i]):
                str0_indices[i] = str0
                e1 = 0
                if h1[i] is not None:
                    e1 = h1[i][occ,occ].sum()
                e2 = 0
                if jdiag[i][i] is not None:
                    e2 += jdiag[i][i][occ][:,occ].sum()
                if kdiag[i] is not None:
                    e2 -= kdiag[i][occ][:,occ].sum()
                hdiag[tuple(str0_indices)] += e1 + e2*.5

    done = [[False] * ndim for _ in range(ndim)]
    for k in range(ndim):
        for l in range(ndim):
            if k != l and jdiag[k][l] is not None and not done[k][l]:
                str0_indices = [slice(None)] * ndim
                jdiag_ = jdiag[k][l]
                for str0, aocc in enumerate(occslists[k]):
                    str0_indices[k] = str0
                    for str1, bocc in enumerate(occslists[l]):
                        e2 = jdiag_[aocc][:,bocc].sum()
                        str0_indices[l] = str1
                        hdiag[tuple(str0_indices)] += e2
                done[k][l] = done[l][k] = True
    print(f'make_hdiag: {time.time() - t0} seconds', flush=True)
    return hdiag.reshape(-1)

def kernel(h1, g2, norb, nparticle, ecore=0, ci0=None, hdiag=None, nroots=1):
    h2 = [[None] * len(norb) for _ in range(len(norb))]
    h2[0][0], h2[0][1], h2[1][1] = absorb_h1e(h1[:2], (g2[0][0], g2[0][1], g2[1][1]),
                                              norb[0], (nparticle[0], nparticle[1]), .5)
    for i in range(len(norb)):
        for j in range(len(norb)):
            if i >= 2 or j >= 2:
                h2[i][j] = g2[i][j]

    def hop(c):
        hc = contract(h1, h2, c, norb, nparticle)
        return hc.reshape(-1)
    if hdiag is None:
        hdiag = make_hdiag(h1, g2, norb, nparticle)
    if ci0 is None:
        dim = []
        for i in range(len(norb)):
            dim.append(cistring.num_strings(norb[i], nparticle[i]))
        print(f'FCI vector shape: {dim}', flush=True)
        print(f'FCI dimension: {hdiag.size}', flush=True)
        addrs = numpy.argpartition(hdiag, nroots-1)[:nroots]
        ci0 = []
        for addr in addrs:
            print(f'{hdiag[addr]=}', flush=True)
            ci0_ = numpy.zeros(hdiag.size)
            ci0_[addr] = 1
            ci0.append(ci0_)

    precond = lambda x, e, *args: x/(hdiag-e+1e-4)
    t0 = time.time()
    converged, e, c = lib.davidson1(lambda xs: [hop(x) for x in xs],
                                    ci0, precond, max_cycle=100,
                                    max_memory=256000, nroots=nroots, verbose=10)
    print(f'davidson: {time.time() - t0} seconds', flush=True)
    if converged[0]:
        print('FCI Davidson converged!')
    else:
        print('FCI Davidson did not converge according to current setting.')
    return e+ecore, c

def energy(h1, g2, fcivec, norb, nparticle, ecore=0):
    h2 = [[None] * len(norb) for _ in range(len(norb))]
    h2[0][0], h2[0][1], h2[1][1] = absorb_h1e(h1[:2], (g2[0][0], g2[0][1], g2[1][1]),
                                              norb[0], (nparticle[0], nparticle[1]), .5)
    for i in range(len(norb)):
        for j in range(len(norb)):
            if i >= 2 or j >= 2:
                h2[i][j] = g2[i][j]
    ci1 = contract(h1, h2, fcivec, norb, nparticle)
    return numpy.dot(fcivec, ci1) + ecore

# dm_pq = <|p^+ q|>
def make_rdm1(fcivec, index, norb, nparticle):
    ndim = len(norb)
    link_index = cistring.gen_linkstr_index(range(norb[index]), nparticle[index])
    dim = []
    for i in range(ndim):
        dim.append(cistring.num_strings(norb[i], nparticle[i]))
    fcivec = fcivec.reshape(dim)
    rdm1 = numpy.zeros((norb[index],norb[index]))
    str0_indices = [slice(None)] * ndim
    str1_indices = [slice(None)] * ndim
    for str0, tab in enumerate(link_index):
        str0_indices[index] = str0
        str0_indices_tuple = tuple(str0_indices)
        for a, i, str1, sign in tab:
            str1_indices[index] = str1
            rdm1[a,i] += sign * numpy.dot(fcivec[tuple(str1_indices)].reshape(-1),
                                          fcivec[str0_indices_tuple].reshape(-1))
    return rdm1

def make_rdm2(fcivec, index1, index2, norb, nparticle):
    assert index1 != index2
    ndim = len(norb)
    link_index1 = cistring.gen_linkstr_index(range(norb[index1]), nparticle[index1])
    link_index2 = cistring.gen_linkstr_index(range(norb[index2]), nparticle[index2])
    dim = []
    for i in range(ndim):
        dim.append(cistring.num_strings(norb[i], nparticle[i]))
    fcivec = fcivec.reshape(dim)
    rdm2 = numpy.zeros((norb[index1],norb[index1],norb[index2],norb[index2]))
    str0_indices = [slice(None)] * ndim
    str1_indices = [slice(None)] * ndim
    for str01, tab1 in enumerate(link_index1):
        str0_indices[index1] = str01
        for p, q, str11, sign1 in tab1:
            str1_indices[index1] = str11
            for str02, tab2 in enumerate(link_index2):
                str0_indices[index2] = str02
                str0_indices_tuple = tuple(str0_indices)
                for r, s, str12, sign2 in tab2:
                    str1_indices[index2] = str12
                    rdm2[p,q,r,s] += sign1 * sign2 \
                                     * numpy.dot(fcivec[tuple(str1_indices)].reshape(-1),
                                                 fcivec[str0_indices_tuple].reshape(-1))
    return rdm2

def energy_decomp(h1, g2, fcivec, norb, nparticle):
    ndim = len(norb)
    for i in range(ndim):
        if h1[i] is not None:
            rdm1 = make_rdm1(fcivec, i, norb, nparticle)
            e1 = numpy.dot(h1[i].reshape(-1), rdm1.reshape(-1))
            print(f'1-body energy for {i}-th particle: {e1}')
    done = [[False] * ndim for _ in range(ndim)]
    for i in range(ndim):
        for j in range(ndim):
            if i != j and g2[i][j] is not None and not done[i][j]:
                rdm2 = make_rdm2(fcivec, i, j, norb, nparticle)
                e2 = numpy.dot(g2[i][j].reshape(-1), rdm2.reshape(-1))
                print(f'2-body energy between {i}-th particle and {j}-th particle: {e2}')
                done[i][j] = done[j][i] = True

def entropy(indices, fcivec, norb, nparticle):
    """Subspace von Neumann entropy.
    indices means the indices you want for the subspace entropy.
    For example, if we have a system of [0, 1, 2, 3],
    indices = [2]
    means we will first get rho[2] = \sum_{0,1,3} rho[0,1,2,3]
    then calculate the entropy via -\sum \lambda ln(\lambda);
    indices = [0,1,2]
    means we will first get rho[0,1,2] = \sum_{3} rho[0,1,2,3]
    then calculate the entropy.
    """
    if isinstance(indices, (int, numpy.integer)):
        indices = [indices]
    ndim = len(norb)
    dim = []
    size = 1
    for i in range(ndim):
        n = cistring.num_strings(norb[i], nparticle[i])
        dim.append(n)
        if i in indices:
            size *= n
    fcivec = fcivec.reshape(dim)

    sum_dims = [i for i in range(ndim) if i not in indices]

    input_subscripts1 = ''.join(chr(97 + i) for i in range(ndim))
    input_subscripts2 = ''.join(chr(97 + i) if i in sum_dims
                                else chr(97 + ndim + i)
                                for i in range(ndim))
    output_subscripts = ''.join(chr(97 + i) for i in indices) \
                      + ''.join(chr(97 + ndim + i) for i in indices)

    einsum_str = f'{input_subscripts1},{input_subscripts2}->{output_subscripts}'
    rdm = numpy.einsum(einsum_str, fcivec, fcivec)
    w = scipy.linalg.eigh(rdm.reshape(size,size), eigvals_only=True)
    w = w[w>1e-16]
    return -(w * numpy.log(w)).sum()

def integrals(mf):
    mol = mf.mol
    h1e_a = reduce(numpy.dot, (mf.mf_elec.mo_coeff[0].T, mf.mf_elec.get_hcore(), mf.mf_elec.mo_coeff[0]))
    h1e_b = reduce(numpy.dot, (mf.mf_elec.mo_coeff[1].T, mf.mf_elec.get_hcore(), mf.mf_elec.mo_coeff[1]))
    h1 = [h1e_a, h1e_b]
    for i in range(mol.nuc_num):
        h1n = reduce(numpy.dot, (mf.mf_nuc[i].mo_coeff.T, mf.mf_nuc[i].get_hcore(), mf.mf_nuc[i].mo_coeff))
        h1.append(h1n)
    eri_ee_aa = ao2mo.kernel(mf.mf_elec._eri,
                             (mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0],
                              mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0]),
                             compact=False)
    eri_ee_ab = ao2mo.kernel(mf.mf_elec._eri,
                             (mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0],
                              mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                             compact=False)
    eri_ee_bb = ao2mo.kernel(mf.mf_elec._eri,
                             (mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1],
                              mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                             compact=False)
    g2 = [[None] * (2+mol.nuc_num) for _ in range(2+mol.nuc_num)]
    g2[0][0] = eri_ee_aa
    g2[0][1] = eri_ee_ab
    g2[1][1] = eri_ee_bb
    for i in range(mol.nuc_num):
        ia = mol.nuc[i].atom_index
        charge = mol.atom_charge(ia)
        eri_ne = -charge * ao2mo.kernel(mf._eri_ne[i],
                                        (mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff,
                                         mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0]),
                                        compact=False)
        g2[i+2][0] = eri_ne
        eri_ne = -charge * ao2mo.kernel(mf._eri_ne[i],
                                        (mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff,
                                         mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                                        compact=False)
        g2[i+2][1] = eri_ne
    for i in range(mol.nuc_num):
        ia = mol.nuc[i].atom_index
        charge_i = mol.atom_charge(ia)
        for j in range(i):
            ja = mol.nuc[j].atom_index
            charge = charge_i * mol.atom_charge(ja)
            eri_nn = charge * ao2mo.kernel(mf._eri_nn[j][i],
                                           (mf.mf_nuc[j].mo_coeff, mf.mf_nuc[j].mo_coeff,
                                            mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff),
                                           compact=False)
            g2[j+2][i+2] = eri_nn
    return h1, g2

def symmetry_finder(mol):
    from pyscf.hessian.thermo import rotation_const, _get_rotor_type
    # find if this is a linear molecule
    mass = mol.mass
    atom_coords = mol.atom_coords()
    mass_center = numpy.einsum('z,zx->x', mass, atom_coords) / mass.sum()
    atom_coords = atom_coords - mass_center
    rot_const = rotation_const(mass, atom_coords, 'GHz')
    rotor_type = _get_rotor_type(rot_const)
    if rotor_type == 'LINEAR':
        # if a linear molecule, detect if the molecule is along x/y/z axis
        if numpy.abs(atom_coords[:,0]).max() < 1e-6:
            if numpy.abs(atom_coords[:,1]).max() < 1e-6:
                axis = 2
            elif numpy.abs(atom_coords[:,2]).max() < 1e-6:
                axis = 1
        elif numpy.abs(atom_coords[:,1]).max() < 1e-6:
            if numpy.abs(atom_coords[:,2]).max() < 1e-6:
                axis = 0
        else:
            # if not along an axis, warn
            print('This is molecule is linear, but was not put along x/y/z axis. Symmetry will be OFF.',
                  flush=True)
            return False, False, None
    else:
        print('Not a linear molecule. Symmetry will be OFF.', flush=True)
        return False, False, None
    print(f'Linear molecule along {chr(axis+88)}', flush=True)
    is_scalar = False
    if mol.natm == 2 and mol.mass[0] == mol.mass[1]:
        is_scalar = True
        for basis1 in mol.nuc[0]._basis.values():
            for basis2 in mol.nuc[1]._basis.values():
                if basis1 != basis2:
                    is_scalar = False
    if is_scalar:
        print('Symmetric diatomic molecule', flush=True)
    # if the linear molecule is a diatomic, see if it is symmetric
    return True, is_scalar, axis

def FCI(mf, kernel=kernel, integrals=integrals, energy=energy):
    assert mf.unrestricted
    norb_e = mf.mf_elec.mo_coeff[0].shape[1]
    mol = mf.mol
    nelec = mol.elec.nelec
    norb = [norb_e, norb_e]
    nparticle = [nelec[0], nelec[1]]
    for i in range(mol.nuc_num):
        norb_n = mf.mf_nuc[i].mo_coeff.shape[1]
        norb.append(norb_n)
        nparticle.append(1)
    print(f'{norb=}', flush=True)
    print(f'{nparticle=}', flush=True)

    is_cneo = False
    if isinstance(mf, neo.CDFT):
        is_cneo = True
        print('CNEO-FCI')
    else:
        print('Unconstrained NEO-FCI')

    h1, g2 = integrals(mf)

    ecore = mf.mf_elec.energy_nuc()

    class CISolver():
        def __init__(self, nroots=1, symmetry=True):
            self.nroots = nroots
            self.symmetry = symmetry
        def kernel(self, h1=h1, g2=g2, norb=norb, nparticle=nparticle,
                   ecore=ecore):
            self.e, self.c = kernel(h1, g2, norb, nparticle, ecore,
                                    nroots=self.nroots)
            return self.e[0], self.c[0]
        def entropy(self, indices, fcivec=None, norb=norb, nparticle=nparticle):
            if fcivec is None:
                fcivec = self.c[0]
            return entropy(indices, fcivec, norb, nparticle)
        def energy_decomp(self, h1=h1, g2=g2, fcivec=None, norb=norb, nparticle=nparticle):
            if fcivec is None:
                fcivec = self.c[0]
            return energy_decomp(h1, g2, fcivec, norb, nparticle)
        def make_rdm1(self, index, fcivec=None, norb=norb, nparticle=nparticle, ao_repr=False):
            assert index >= 0 and index < len(norb)
            if fcivec is None:
                fcivec = self.c[0]
            rdm1 = make_rdm1(fcivec, index, norb, nparticle)
            if ao_repr:
                if index == 0:
                    coeff = mf.mf_elec.mo_coeff[0]
                elif index == 1:
                    coeff = mf.mf_elec.mo_coeff[1]
                else:
                    coeff = mf.mf_nuc[index-2].mo_coeff
                rdm1 = reduce(numpy.dot, (coeff, rdm1, coeff.T))
            return rdm1
        def make_natorbs(self, index, fcivec=None, norb=norb, nparticle=nparticle):
            assert index >= 0 and index < len(norb)
            if fcivec is None:
                fcivec = self.c[0]
            rdm1 = make_rdm1(fcivec, index, norb, nparticle)
            if index == 0:
                coeff = mf.mf_elec.mo_coeff[0]
            elif index == 1:
                coeff = mf.mf_elec.mo_coeff[1]
            else:
                coeff = mf.mf_nuc[index-2].mo_coeff
            # pyscf.mp.dfmp2_native
            eigval, eigvec = numpy.linalg.eigh(rdm1)
            natocc = numpy.flip(eigval)
            w = natocc[natocc>1e-16]
            print(f'Natocc entropy: {-(w * numpy.log(w)).sum()}')
            natorb = lib.dot(coeff, numpy.fliplr(eigvec))
            return natocc, natorb

    if is_cneo and mol.natm > 1:
        r1 = []
        for i in range(mol.nuc_num):
            r1n = []
            for x in range(mf.mf_nuc[i].int1e_r.shape[0]):
                r1n.append(reduce(numpy.dot, (mf.mf_nuc[i].mo_coeff.T, mf.mf_nuc[i].int1e_r[x],
                                              mf.mf_nuc[i].mo_coeff)))
            r1n = numpy.array(r1n)
            r1.append(r1n)
        class CCISolver(CISolver):
            def position_analysis(self, f, h1, r1, g2, norb, nparticle, ecore):
                if self.symmetry:
                    f_ = numpy.zeros((len(norb)-2,3))
                    f_[:,self.axis] = f
                    if self.scalar:
                        f_[1,self.axis] = -f_[0,self.axis]
                    f = f_
                else:
                    f = f.reshape((len(norb)-2,3))
                self.last_f = f
                if self.hdiag is None:
                    self.hdiag = make_hdiag(h1, g2, norb, nparticle)
                    print(f'hdiag: {self.hdiag[:4]}', flush=True)
                f1 = [None] * len(norb)
                for i in range(len(norb)-2):
                    f1[i+2] = numpy.einsum('xij,x->ij', r1[i], f[i])
                fr = make_hdiag(f1, None, norb, nparticle)
                f1[0] = h1[0]
                f1[1] = h1[1]
                for i in range(len(norb)-2):
                    f1[i+2] += h1[i+2]
                self.e, self.c = kernel(f1, g2, norb, nparticle, ecore,
                                        self.c, self.hdiag + fr, self.nroots)
                t0 = time.time()
                dr = numpy.empty_like(f)
                for i in range(len(norb)-2):
                    rdm1 = make_rdm1(self.c[0], i+2, norb, nparticle)
                    dr[i] = numpy.einsum('xij,ij->x', r1[i], rdm1)
                print(f'dr: {time.time() - t0} seconds', flush=True)
                print()
                print(f'CNEO| {f=}')
                print(f'CNEO| lowest eigenvalue of H+f(r-R)={self.e[0]}')
                print(f'CNEO| max|dr|={numpy.abs(dr).max()}')
                print()
                if self.symmetry:
                    if self.scalar:
                        dr = dr[0,self.axis].item()
                    else:
                        dr = dr[:,self.axis].ravel()
                else:
                    dr = dr.ravel()
                return dr

            def kernel(self, h1=h1, r1=r1, g2=g2, norb=norb, nparticle=nparticle,
                       ecore=ecore):
                # get initial f from CNEO-HF
                # NOTE: for high angular momentum basis, CNEO-HF guess can be bad
                f = numpy.zeros((mol.nuc_num, 3))
                for i in range(mol.nuc_num):
                    ia = mol.nuc[i].atom_index
                    f[i] = mf.f[ia]
                self.c = None
                self.hdiag = None
                self.scalar = False
                self.axis = None
                if self.symmetry:
                    self.symmetry, self.scalar, self.axis = symmetry_finder(mol)
                skip_root = False
                if self.symmetry and self.scalar:
                    skip_root = True # because now using root_scalar
                    f0 = f[0,self.axis].item()
                    dr0 = self.position_analysis(f0, h1, r1, g2, norb, nparticle, ecore)
                    if abs(dr0) < 1e-6:
                        root = f0
                    else:
                        # first determine the bracket
                        if abs(f0) < 1e-3:
                            if f0 > 0:
                                bracket = [0., 10.*f0]
                            else:
                                bracket = [10.*f0, 0.]
                            dr1 = self.position_analysis(bracket[0], h1, r1, g2, norb, nparticle, ecore)
                            dr2 = self.position_analysis(bracket[1], h1, r1, g2, norb, nparticle, ecore)
                            while dr1 * dr2 > 0:
                                bracket[0] -= 100.*abs(f0)
                                bracket[1] += 100.*abs(f0)
                                dr1 = self.position_analysis(bracket[0], h1, r1, g2, norb, nparticle, ecore)
                                dr2 = self.position_analysis(bracket[1], h1, r1, g2, norb, nparticle, ecore)
                        else:
                            if f0 > 0:
                                bracket = [0., f0]
                                dr1 = self.position_analysis(bracket[0], h1, r1, g2, norb, nparticle, ecore)
                                dr2 = dr0
                            else:
                                bracket = [f0, 0.]
                                dr1 = dr0
                                dr2 = self.position_analysis(bracket[1], h1, r1, g2, norb, nparticle, ecore)
                            while dr1 * dr2 > 0:
                                bracket[0] -= 0.1
                                bracket[1] += 0.1
                                dr1 = self.position_analysis(bracket[0], h1, r1, g2, norb, nparticle, ecore)
                                dr2 = self.position_analysis(bracket[1], h1, r1, g2, norb, nparticle, ecore)
                        print(f'Initial f range: {bracket}')
                        sol = scipy.optimize.root_scalar(self.position_analysis,
                                                         args=(h1, r1, g2, norb, nparticle, ecore),
                                                         bracket=bracket, xtol=1e-6)
                        if sol.converged:
                            print('Lagrange multiplier optimization succeeded!')
                        else:
                            print('Lagrange multiplier optimization failed!')
                        print(f'Root finding message: {sol.flag}')
                        root = sol.root
                    f = numpy.zeros((mol.nuc_num,3))
                    f[0,self.axis] = root
                    f[1,self.axis] = -root
                else:
                    if self.symmetry:
                        # test if zero guess is a better guess
                        dr0 = self.position_analysis(f[:,self.axis].ravel(), h1, r1, g2, norb, nparticle, ecore)
                        if numpy.abs(dr0).max() < 1e-6:
                            root = f[:,self.axis]
                            skip_root = True
                        else:
                            dr1 = self.position_analysis(numpy.zeros_like(f[:,self.axis].ravel()),
                                                         h1, r1, g2, norb, nparticle, ecore)
                            if numpy.abs(dr1).max() < 1e-6:
                                root = numpy.zeros_like(f[:,self.axis])
                                skip_root = True
                            else:
                                if numpy.abs(dr1).max() < numpy.abs(dr0).max():
                                    f = numpy.zeros_like(f)
                                print(f'Initial f: {f}')
                                sol = scipy.optimize.root(self.position_analysis, f[:,self.axis].ravel(),
                                                          args=(h1, r1, g2, norb, nparticle, ecore),
                                                          method='hybr', options={'eps': 1e-2})
                                root = sol.x
                        f = numpy.zeros((mol.nuc_num,3))
                        f[:,self.axis] = root
                    else:
                        # test if zero guess is a better guess
                        dr0 = self.position_analysis(f.ravel(), h1, r1, g2, norb, nparticle, ecore)
                        if numpy.abs(dr0).max() < 1e-6:
                            skip_root = True
                        else:
                            dr1 = self.position_analysis(numpy.zeros_like(f.ravel()),
                                                         h1, r1, g2, norb, nparticle, ecore)
                            if numpy.abs(dr1).max() < 1e-6:
                                f = numpy.zeros_like(f)
                                skip_root = True
                            else:
                                if numpy.abs(dr1).max() < numpy.abs(dr0).max():
                                    f = numpy.zeros_like(f)
                                print(f'Initial f: {f}')
                                sol = scipy.optimize.root(self.position_analysis, f.ravel(),
                                                          args=(h1, r1, g2, norb, nparticle, ecore),
                                                          method='hybr', options={'eps': 1e-2})
                                f = sol.x.reshape((mol.nuc_num,3))
                    if not skip_root:
                        if sol.success:
                            print('Lagrange multiplier optimization succeeded!')
                        else:
                            print('Lagrange multiplier optimization failed!')
                        print(f'Root finding message: {sol.message}')
                print(f'CNEO| Final: Optimized f: {f}')
                if not skip_root:
                    if self.symmetry:
                        print(f'CNEO| Final: Position deviation: {sol.fun}')
                    else:
                        print(f'CNEO| Final: Position deviation: {sol.fun.reshape((mol.nuc_num,3))}')
                    print(f'CNEO| Final: Position deviation max: {numpy.abs(sol.fun).max()}')
                print()
                print(f'CNEO| Last f: {self.last_f}')
                eigenvalue = self.e[0]
                print(f'CNEO| Energy directly from the eigenvalue of H+f(r-R) matrix (with last f): {self.e[0]}')
                self.e[0] = energy(h1, g2, self.c[0], norb, nparticle, ecore)
                print(f'CNEO| Energy recalculated using c^T*H*c: {self.e[0]}, difference={self.e[0]-eigenvalue}')
                return self.e[0], self.c[0], f
        cisolver = CCISolver()
    else:
        cisolver = CISolver()
    return cisolver


if __name__ == '__main__':
    mol = neo.M(atom='H 0 0 0', basis='aug-ccpvdz',
                nuc_basis='pb4d', charge=0, spin=1)
    mol.verbose = 0
    mol.output = None

    mf = neo.HF(mol, unrestricted=True)
    mf.conv_tol_grad = 1e-7
    mf.kernel()
    print(f'HF energy: {mf.e_tot}', flush=True)
    e1 = FCI(mf).kernel()[0]
    print(f'FCI energy: {e1}, difference with benchmark: {e1 - -0.4777448729395}')
