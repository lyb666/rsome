from .lp import Model as LPModel
from .lp import LinConstr, Bounds, CvxConstr, ConeConstr
from .lp import Vars, VarSub, Affine, Convex, LinProg
import numpy as np
import pandas as pd
import scipy.sparse as sp
from numbers import Real
from scipy.sparse import csr_matrix
from collections import Iterable


class Model(LPModel):
    """
    The Model class creates an SOCP model object
    """

    def __init__(self, nobj=False, mtype='R', name=None):

        super().__init__(nobj, mtype, name)
        self.cvx_constr = []
        self.cone_constr = []
        # self.avar_indices = []

    def reset(self):

        self.lin_constr = []
        self.pws_constr = []
        self.cone_constr = []
        self.bounds = []
        self.aux_constr = []
        self.aux_bounds = []
        self.cvx_constr = []

    def st(self, constr):

        if isinstance(constr, Iterable):
            for item in constr:
                self.st(item)
        elif isinstance(constr, (LinConstr, Bounds)):
            super().st(constr)
        elif isinstance(constr, CvxConstr):
            if constr.model is not self:
                raise ValueError('Constraints are not defined for this model.')
            if constr.xtype in 'AMI':
                super().st(constr)
            elif constr.xtype in 'ESQ':
                self.cvx_constr.append(constr)
            else:
                raise TypeError('Incorrect constraint type.')
        elif isinstance(constr, ConeConstr):
            if constr.model is not self:
                raise ValueError('Constraints are not defined for this model.')
            self.cone_constr.append(constr)
        else:
            raise TypeError('Unknown constraint type.')

        self.pupdate = True
        self.dupdate = True

    def do_math(self, primal=True, refresh=True, obj=False):

        if primal:
            if self.primal is not None and not self.pupdate:
                return self.primal

            if refresh:
                self.auxs = []
                self.aux_constr = []
                self.aux_bounds = []
                self.last = self.vars[-1].first + self.vars[-1].size

            more_cvx = []
            if self.obj:
                obj_constr = (self.vars[0] >= self.sign * self.obj)
                if isinstance(obj_constr, CvxConstr):
                    more_cvx.append(obj_constr)

            qmat = []
            for constr in self.cvx_constr + more_cvx:
                if constr.xtype == 'E':
                    aux_left = self.dvar(constr.affine_in.shape, aux=True)
                    aux_right = self.dvar(constr.affine_out.shape, aux=True)
                    self.aux_constr.append(constr.affine_in - aux_left == 0)
                    self.aux_constr.append(constr.affine_out - aux_right == 0)
                    # self.aux_bounds.append(aux_right >= 0)
                    qmat.append([aux_right.first] +
                                list([aux_left.first + index
                                      for index in range(aux_left.size)]))
                elif constr.xtype == 'S':
                    aux1 = self.dvar(constr.affine_out.shape, aux=True)
                    aux2 = self.dvar(constr.affine_in.shape, aux=True)
                    aux3 = self.dvar(constr.affine_out.shape, aux=True)
                    self.aux_constr.append(aux1 - 0.5*(1+constr.affine_out)
                                           == 0)
                    self.aux_constr.append(aux2 - constr.affine_in == 0)
                    self.aux_constr.append(aux3 - 0.5 * (1-constr.affine_out)
                                           == 0)
                    self.aux_bounds.append(aux3 >= 0)
                    for i in range(constr.affine_in.size):
                        qmat.append([aux3.first + i] +
                                    [aux1.first + i, aux2.first + i])
                elif constr.xtype == 'Q':
                    aux1 = self.dvar(constr.affine_out.shape, aux=True)
                    aux2 = self.dvar(constr.affine_in.shape, aux=True)
                    aux3 = self.dvar(constr.affine_out.shape, aux=True)
                    self.aux_constr.append(aux1 - 0.5 * (1+constr.affine_out)
                                           == 0)
                    self.aux_constr.append(aux2 - constr.affine_in == 0)
                    self.aux_constr.append(aux3 - 0.5 * (1-constr.affine_out)
                                           == 0)
                    self.aux_bounds.append(aux3 >= 0)
                    qmat.append([aux3.first] + [aux1.first] +
                                list(aux2.first + np.arange(aux2.size)))

            for constr in self.cone_constr:
                qmat.append([constr.right_var.first + constr.right_index] +
                            [constr.left_var.first + index
                             for index in constr.left_index])

            formula = super().do_math(primal=True, refresh=False, obj=obj)
            formula = SOCProg(formula.linear, formula.const, formula.sense,
                              formula.vtype, formula.ub, formula.lb,
                              qmat, formula.obj)
            self.primal = formula
            self.pupdate = False

            return formula
        else:
            if self.dual is not None and not self.dupdate:
                return self.dual

            primal = self.do_math()

            dual_lp = super().do_math(primal=False, refresh=False)
            if len(primal.qmat) == 0:
                return SOCProg(dual_lp.linear, dual_lp.const, dual_lp.sense,
                               dual_lp.vtype, dual_lp.ub, dual_lp.lb,
                               [], dual_lp.obj)

            eye_indices = [item for inner in primal.qmat for item in inner]
            lin_indices = [ind for ind in range(primal.linear.shape[1])
                           if ind not in eye_indices]
            linear = dual_lp.linear[lin_indices, :]
            const = dual_lp.const[lin_indices]
            sense = dual_lp.sense[lin_indices]
            obj = dual_lp.obj
            vtype = dual_lp.vtype
            ub = dual_lp.ub
            lb = dual_lp.lb
            qmat = []
            for qc in primal.qmat:
                ub[dual_lp.linear[qc[0], :].indices] = 0
                qmat.append(list(dual_lp.linear[qc, :].indices))

            formula = SOCProg(linear, const, sense,
                              vtype, ub, lb, qmat, obj)

            self.dual = formula
            self.dupdate = False

            return formula


class SOCProg(LinProg):
    """
    The SOCProg class creates an second-order cone program
    """

    def __init__(self, linear, const, sense, vtype, ub, lb, qmat, obj=None):

        super().__init__(linear, const, sense, vtype, ub, lb, obj)
        self.qmat = qmat

    def __repr__(self):

        qmat = self.qmat
        string = 'Second order cone program object:\n'
        string += super().__repr__(header=False)
        string += '---------------------------------------------\n'
        string += 'Number of SOC constraints:    {0}\n'.format(len(qmat))

        return string

    def showqc(self):

        n = len(self.qmat)
        indices = np.concatenate([item for item in self.qmat])
        values = np.concatenate([[-1.0] + [1.0]*(len(item)-1)
                                 for item in self.qmat])
        indptr = [0] * (n + 1)
        for i in range(n):
            indptr[i+1] = indptr[i] + len(self.qmat[i])

        var_names = ['q{0}'.format(i)
                     for i in range(1, self.linear.shape[1] + 1)]
        constr_names = ['QC{0}'.format(j)
                        for j in range(1, n + 1)]
        table = pd.DataFrame(sp.csr_matrix((values, indices, indptr),
                             (n, self.linear.shape[1])).todense(),
                             index=constr_names, columns=var_names)
        table['sense'] = ['<='] * n
        table['constants'] = [0.0] * n

        return table