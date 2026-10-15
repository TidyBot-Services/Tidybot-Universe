"""Defined C2 command interpolation of public samples at their original times.

This is an adapter contract, not a reconstruction of the solver's internal
trajectory. It preserves every returned position/velocity/acceleration sample.
No time scaling, new waypoint, or relaxation is used to obtain acceptance.
"""
import math
import numpy as np
from .client import PlanningRejected

class CommandCurve:
    schema='attentionbench.command-curve.quintic-hermite.v1'
    def __init__(self,response):
        if response.get('status')!='success':raise PlanningRejected('Only actual successful public trajectories can define a command curve')
        self.names=response['joint_names'];self.q=np.asarray(response['trajectory'],float)
        self.times=np.asarray(response.get('time_from_start_seconds'),float)
        d=response.get('trajectory_derivatives')or{}
        self.v=np.asarray(d.get('velocity'),float);self.a=np.asarray(d.get('acceleration'),float)
        if(len(self.names)!=len(set(self.names))or self.q.ndim!=2 or len(self.q)<2 or self.q.shape[1]!=len(self.names)
                or self.v.shape!=self.q.shape or self.a.shape!=self.q.shape or self.times.shape!=(len(self.q),)
                or not all(np.isfinite(x).all()for x in [self.q,self.v,self.a,self.times])
                or self.times[0]!=0 or np.any(np.diff(self.times)<=0)):
            raise PlanningRejected('Command interpolation requires finite public q/v/a and strictly increasing actual timestamps')
        if np.any(self.v[[0,-1]]!=0)or np.any(self.a[[0,-1]]!=0):
            raise PlanningRejected('A stationary native hold requires public zero endpoint velocity and acceleration')
        self.h=np.diff(self.times);h=self.h[:,None]
        coefficients=np.zeros((len(self.h),6,len(self.names)))
        coefficients[:,0]=self.q[:-1];coefficients[:,1]=h*self.v[:-1];coefficients[:,2]=h*h*self.a[:-1]/2
        r0=self.q[1:]-coefficients[:,0]-coefficients[:,1]-coefficients[:,2]
        r1=h*self.v[1:]-coefficients[:,1]-2*coefficients[:,2]
        r2=h*h*self.a[1:]-2*coefficients[:,2]
        coefficients[:,3]=10*r0-4*r1+r2/2
        coefficients[:,4]=-15*r0+7*r1-r2
        coefficients[:,5]=6*r0-3*r1+r2/2
        if not np.isfinite(coefficients).all():raise PlanningRejected('Nonfinite command curve coefficients')
        self.coefficients=coefficients

    def evaluate(self,times,order=0):
        values=np.asarray(times,float)
        if values.ndim!=1 or not np.isfinite(values).all()or np.any(values<0):raise PlanningRejected('Invalid command sample times')
        out=[]
        for t in values:
            if t>self.times[-1]:out.append(self.q[-1]if order==0 else np.zeros(len(self.names)));continue
            index=min(len(self.h)-1,max(0,int(np.searchsorted(self.times,t,side='right')-1)))
            u=(t-self.times[index])/self.h[index]
            c=np.polynomial.polynomial.polyder(self.coefficients[index],m=order,axis=0)/self.h[index]**order
            out.append(np.polynomial.polynomial.polyval(u,c))
        return np.array(out)

    def ranges(self,order=0):
        lower=np.full(len(self.names),np.inf);upper=-lower.copy()
        for index,h in enumerate(self.h):
            coefficient=np.polynomial.polynomial.polyder(self.coefficients[index],m=order,axis=0)/h**order
            for joint in range(len(self.names)):
                c=coefficient[:,joint];dc=np.polynomial.polynomial.polyder(c)
                roots=np.polynomial.polynomial.polyroots(dc)if np.any(dc)else[]
                points=[0.,1.]+[float(z.real)for z in roots if abs(z.imag)<1e-9 and 0<z.real<1]
                values=np.polynomial.polynomial.polyval(points,c)
                padding=64*np.finfo(float).eps*float(np.sum(abs(c)))if np.any(c[1:])else 0.
                lower[joint]=min(lower[joint],float(min(values))-padding)
                upper[joint]=max(upper[joint],float(max(values))+padding)
        return lower,upper

    def certify_motion(self,*,position_limits,maximum_speed,max_acceleration,max_jerk):
        limits=np.asarray(position_limits,float);speed=np.asarray([maximum_speed[n]for n in self.names],float)
        if(limits.shape!=(len(self.names),2)or not np.isfinite(limits).all()or np.any(limits[:,0]>=limits[:,1])
                or not np.isfinite(speed).all()or np.any(speed<=0)
                or not all(isinstance(x,(int,float))and not isinstance(x,bool)and math.isfinite(x)and x>0 for x in [max_acceleration,max_jerk])):
            raise PlanningRejected('Invalid authoritative native motion bounds')
        low,high=self.ranges()
        if np.any(low<limits[:,0])or np.any(high>limits[:,1]):raise PlanningRejected('Command curve exceeds native joint limits between public samples')
        maxima={};bounds={'velocity':speed,'acceleration':np.full(len(self.names),max_acceleration),'jerk':np.full(len(self.names),max_jerk)}
        for order,name in enumerate(bounds,1):
            lo,hi=self.ranges(order);maximum=np.maximum(abs(lo),abs(hi))
            if np.any(maximum>bounds[name]):raise PlanningRejected('Command curve exceeds '+name+' bounds; official times will not be changed')
            maxima[name]=dict(maximum_absolute=maximum.tolist(),limits=bounds[name].tolist())
        return dict(schema=self.schema,result='PASS',duration_seconds=float(self.times[-1]),joint_names=self.names,
                    position_minimum=low.tolist(),position_maximum=high.tolist(),derivatives=maxima,
                    source='Public q/v/a samples at unchanged public timestamps',collision_scope='Not certified by this motion-only check')

def sample_for_original_drive(response,*,joint_names,maximum_speed,position_limits,max_acceleration,max_jerk,dt,step_budget,settle_steps):
    if(type(step_budget)is not int or type(settle_steps)is not int or not 0<settle_steps<step_budget
            or isinstance(dt,bool)or not isinstance(dt,(int,float))or not math.isfinite(dt)or dt<=0):
        raise PlanningRejected('Invalid native physics timing/step budget')
    curve=CommandCurve(response)
    if curve.names!=joint_names:raise PlanningRejected('Native command joint order changed')
    certificate=curve.certify_motion(position_limits=position_limits,maximum_speed=maximum_speed,max_acceleration=max_acceleration,max_jerk=max_jerk)
    steps=math.ceil(float(curve.times[-1])/dt)
    if steps+settle_steps>step_budget:raise PlanningRejected('Official trajectory time exceeds native action budget; no retiming is permitted')
    sample_times=np.arange(steps+1)*dt;samples=curve.evaluate(sample_times)
    return samples,dict(travel_steps=steps,settle_steps=step_budget-steps,step_budget=step_budget,dt=dt,
        official_duration_seconds=float(curve.times[-1]),official_time_from_start_seconds=curve.times.tolist(),
        sample_time_from_start_seconds=sample_times.tolist(),time_scale=1.,motion_certificate=certificate,
        source='Unchanged official timestamps; C2 public-sample command interpolation and stationary endpoint hold')
