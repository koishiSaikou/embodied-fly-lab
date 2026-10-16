"""Right-foreleg-to-head engineering motion, applied through real position actuators.

IK is planned on scratch MjData. Live qpos is never edited or teleported.
The other five feet retain adhesion while the right front foot performs a small sweep.
"""
import math
import numpy as np
import mujoco as mj
from scipy.optimize import least_squares
from play_fly import LocomotionAction, PreprogrammedSteps


class GroomingMotor:
    def __init__(self,fly,sim,timestep):
        self.fly,self.sim,self.timestep=fly,sim,timestep
        self.order=fly.get_actuated_jointdofs_order('position')
        self.neutral=PreprogrammedSteps().default_pose_by_dof_order(self.order)
        self.weight=0.;self.phase=0.;self.targets=None;self.planning_errors=[];self.settle=0.
        self.front_indices=[i for i,j in enumerate(self.order) if j.child.name.startswith("rf_")]
        self.adhesion_index=fly.get_legs_order().index("rf")

    def prepare(self):
        model=self.sim.mj_model;scratch=mj.MjData(model)
        scratch.qpos[:]=self.sim.mj_data.qpos;mj.mj_forward(model,scratch)
        head=mj.mj_name2id(model,mj.mjtObj.mjOBJ_BODY,self.fly.name+'/c_head')
        thorax=mj.mj_name2id(model,mj.mjtObj.mjOBJ_BODY,self.fly.name+'/c_thorax')
        head_position=scratch.xpos[head].copy();rotation=scratch.xmat[thorax].reshape(3,3).copy()
        hold=np.array([scratch.qpos[model.jnt_qposadr[mj.mj_name2id(model,mj.mjtObj.mjOBJ_JOINT,self.fly.name+'/'+j.name)]] for j in self.order])
        targets=np.stack([hold.copy(),hold.copy()])
        for leg,sign in [('rf',-1)]:
            indices=[i for i,j in enumerate(self.order) if j.child.name.startswith(leg+'_')]
            joints=[mj.mj_name2id(model,mj.mjtObj.mjOBJ_JOINT,self.fly.name+'/'+self.order[i].name) for i in indices]
            addresses=model.jnt_qposadr[joints]
            foot=mj.mj_name2id(model,mj.mjtObj.mjOBJ_BODY,self.fly.name+'/'+leg+'_tarsus5')
            reference=scratch.qpos[addresses].copy()
            lower=np.array([model.jnt_range[j,0] if model.jnt_limited[j] else -math.pi for j in joints])
            upper=np.array([model.jnt_range[j,1] if model.jnt_limited[j] else math.pi for j in joints])
            for phase in range(2):
                target=head_position+rotation@np.array([.10,sign*.45,-.08+.14*phase])
                def residual(angles):
                    scratch.qpos[addresses]=angles;mj.mj_forward(model,scratch)
                    return np.concatenate((scratch.xpos[foot]-target,.018*(angles-reference)))
                solved=least_squares(residual,np.clip(reference,lower+1e-5,upper-1e-5),bounds=(lower,upper),max_nfev=100)
                targets[phase,indices]=solved.x
                self.planning_errors.append(float(np.linalg.norm(residual(solved.x)[:3])))
        self.hold=hold
        self.targets=targets

    def action(self,action,active,allow=True,emergency=False):
        if not allow or emergency:
            self.weight=0.;self.settle=0.;self.targets=None;self.phase=0.;return action
        if active and self.targets is None:
            self.settle+=self.timestep
            if self.settle<.25:return action
        if not active:self.settle=0.
        if not active and self.weight<=0:self.targets=None;self.phase=0.
        if active:self.weight=min(1.,self.weight+self.timestep/.4)
        else:self.weight=max(0.,self.weight-self.timestep/.4)
        if self.weight<=0:return action
        if self.targets is None:self.prepare()
        self.phase+=self.timestep
        mix=(1-math.cos(self.phase*2*math.pi*2))/2
        target=self.targets[0]*(1-mix)+self.targets[1]*mix
        # Raise the right front foot; the other five feet support this first grooming pattern.
        target_all=self.hold.copy();adhesion=np.ones(6,dtype=bool)
        target_all[self.front_indices]=target[self.front_indices]
        if self.weight>.15:adhesion[self.adhesion_index]=False
        angles=(1-self.weight)*action.joint_angles+self.weight*target_all
        return LocomotionAction(joint_angles=angles,adhesion_onoff=adhesion)
