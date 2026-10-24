"""Pure finite-domain indoor generator fixture; never imported on the host."""

def rect(obj, assets):
    a=assets[obj['asset']]
    w,d=a['width'],a['depth']
    if obj['yaw_deg']%180: w,d=d,w
    return (obj['x']-w/2,obj['y']-d/2,obj['x']+w/2,obj['y']+d/2)

def overlap(a,b):
    return min(a[2],b[2])-max(a[0],b[0])>1e-8 and min(a[3],b[3])-max(a[1],b[1])>1e-8

def clear(obj,placed,p):
    r=rect(obj,p['spec']['assets'])
    if any(overlap(r,rect(o,p['spec']['assets'])) for o in placed): return False
    for c in p['spec']['constraints']:
        if c['type']=='keep_clear':
            z=next(v for v in p['spec']['regions'] if v['id']==c['region'])
            if overlap(r,(z['x'],z['y'],z['x']+z['width'],z['y']+z['depth'])): return False
    return True

def relation(c,objects,p):
    if c['subject'] not in objects: return True
    o=objects[c['subject']];r=rect(o,p['spec']['assets'])
    if c['type']=='near_wall':
        gap={'north':p['depth']-r[3],'south':r[1],'east':p['width']-r[2],'west':r[0]}[c['wall']]
        return -1e-8<=gap<=c['max_gap']+1e-8
    if c['type']=='in_front_of':
        if c['reference'] not in objects: return True
        ref=objects[c['reference']];rr=rect(ref,p['spec']['assets'])
        f={0:(0,1),90:(-1,0),180:(0,-1),270:(1,0)}[ref['yaw_deg']]
        dx,dy=o['x']-ref['x'],o['y']-ref['y']
        half=lambda q,ax:abs(ax[0])*(q[2]-q[0])/2+abs(ax[1])*(q[3]-q[1])/2
        gap=dx*f[0]+dy*f[1]-half(r,f)-half(rr,f)
        lat=(f[1],-f[0]);off=abs(dx*lat[0]+dy*lat[1])
        return -1e-8<=gap<=c['max_gap']+1e-8 and off<=half(r,lat)+half(rr,lat)+1e-8
    return True

def layout(p):
    placed=list(p['fixed']);objects={o['id']:o for o in placed}
    first={}
    for c in p['spec']['constraints']:
        if c['type']=='near_wall' and c['subject'] not in first: first[c['subject']]=c
    names=sorted(p['domains'],key=lambda n:(n not in first,n))
    for name in names:
        for o in p['domains'][name]:
            candidate=dict(objects);candidate[name]=o
            if clear(o,placed,p) and (name not in first or relation(first[name],candidate,p)):
                placed.append(o);objects[name]=o;break
    return {'scene_id':p['spec']['scene_id'],'objects':placed}
