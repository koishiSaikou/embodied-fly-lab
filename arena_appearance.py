"""Small non-colliding props shared by MuJoCo, actual eyes and web geometry.

Eight primitive parts per logical slot avoid model recompilation during edits.
Sizes/positions are millimetres; types are MuJoCo ellipsoid=4, cylinder=5, box=6.
Circular sensory footprints remain defined independently in arena_elements.
"""
import math
from arena_elements import MAX_ELEMENTS, geom_name

PARTS_PER_ELEMENT = 8


def part_name(slot, part):
    return geom_name(slot) if part == 0 else f'arena_prop_{slot}_{part}'


def element_geom_names():
    return {part_name(s, p) for s in range(MAX_ELEMENTS) for p in range(PARTS_PER_ELEMENT)}


def prop_parts(element):
    parts = []
    if element:
        x, y, r = element['x'], element['y'], element['radius']
        u = min(r, 2.5)
        alpha = .35 + .65 * element['strength']

        def add(kind, pos, size, color, angle=0, axis='z'):
            quat = [math.cos(angle/2), 0., 0., 0.]
            quat[{'x': 1, 'y': 2, 'z': 3}[axis]] = math.sin(angle/2)
            parts.append(dict(type=kind, pos=[x+pos[0], y+pos[1], pos[2]],
                              size=list(size), quat=quat, rgba=[*color, alpha]))

        cream, amber = (.98, .94, .80), (.63, .37, .12)
        dark, yellow = (.13, .16, .18), (1., .76, .08)
        kind = element['kind']
        if kind == 'sugar':
            add(6, (0, 0, .045), (.76*r, .58*r, .045), amber)
            for dx, dy, yaw in [(-.32, -.18, -.15), (.28, -.12, .12), (0, .35, -.08)]:
                add(6, (dx*r, dy*r, .23*u+.09), (.22*u, .22*u, .23*u), cream, yaw)
        elif kind == 'fruit':
            add(4, (-.20*r, 0, .39*u), (.43*r, .46*r, .39*u), (.91, .19, .09))
            add(4, (.23*r, .02*r, .37*u), (.40*r, .43*r, .37*u), (1., .39, .12))
            add(5, (.02*r, 0, .85*u), (.055*u, .18*u, 0), amber, -.25, 'y')
            add(4, (.22*r, 0, 1.00*u), (.30*u, .12*u, .045*u), (.20, .55, .16), -.3, 'y')
        elif kind == 'cool':
            add(6, (-.22*r, 0, .30*u), (.34*r, .34*r, .30*u), (.53, .82, .94), .12)
            add(6, (.37*r, .12*r, .20*u), (.24*r, .27*r, .20*u), (.70, .91, 1.), -.2)
            add(6, (-.27*r, -.345*r, .37*u), (.13*r, .008, .12*u), (.88, .98, 1.), .12)
        elif kind == 'water':
            add(4, (-.12*r, 0, .045), (.82*r, .55*r, .04), (.15, .56, .85))
            add(4, (.40*r, .26*r, .04), (.42*r, .36*r, .035), (.15, .56, .85))
            add(4, (-.20*r, -.11*r, .087), (.45*r, .035*u, .012), (.68, .91, 1.), -.15)
            add(4, (.18*r, .20*r, .086), (.20*r, .025*u, .012), (.68, .91, 1.))
        elif kind == 'danger':
            # Upright triangular warning sign, facing south; bar endpoints form a triangle.
            add(6, (0, .12*r, .07), (.46*r, .27*r, .07), dark)
            add(5, (0, .12*r, .43*u), (.055*u, .36*u, 0), dark)
            for a, b in [((-.60, .63), (.60, .63)), ((.60, .63), (0, 1.64)), ((0, 1.64), (-.60, .63))]:
                dx, dz = b[0]-a[0], b[1]-a[1]
                add(6, ((a[0]+b[0])/2*u, .12*r, (a[1]+b[1])/2*u),
                    (math.hypot(dx, dz)*u/2+.025*u, .065*u, .065*u), yellow, -math.atan2(dz, dx), 'y')
            add(6, (0, .12*r, 1.14*u), (.07*u, .075*u, .20*u), yellow)
            add(4, (0, .12*r, .80*u), (.075*u, .08*u, .075*u), yellow)
        elif kind == 'light':
            add(5, (0, 0, .08), (.36*r, .08, 0), dark)
            add(5, (0, 0, .62*u), (.055*u, .58*u, 0), dark)
            add(5, (0, 0, 1.14*u), (.14*u, .12*u, 0), amber)
            add(4, (0, 0, 1.49*u), (.35*u, .35*u, .36*u), (1., .91, .39))
            add(4, (-.12*u, -.24*u, 1.61*u), (.075*u, .045*u, .11*u), (1., 1., .91))
        elif kind == 'shade':
            for dx, dy in [(-.53, -.48), (.53, -.48), (-.53, .48), (.53, .48)]:
                add(6, (dx*r, dy*r, .68*u), (.045*u, .045*u, .68*u), amber)
            add(6, (0, 0, 1.40*u), (.76*r, .68*r, .06*u), (.22, .43, .46))
            add(6, (0, 0, 1.48*u), (.08*r, .68*r, .025*u), (.73, .84, .72))
        else:
            add(6, (0, 0, .04), (.64*r, .64*r, .04), (.35, .39, .41))
            for dx, dy, color in [(-1, -1, cream), (1, -1, dark), (-1, 1, dark), (1, 1, cream)]:
                add(6, (.29*r*dx, .29*r*dy, .09), (.28*r, .28*r, .015), color)
    assert len(parts) <= PARTS_PER_ELEMENT
    while len(parts) < PARTS_PER_ELEMENT:
        parts.append(dict(type=6, pos=[0, 0, -5], size=[.001]*3,
                          quat=[1., 0., 0., 0.], rgba=[0., 0., 0., 0.]))
    return parts


def bounding_radius(part):
    a, b, c = part['size']
    if part['type'] == 4:
        return max(a, b, c)
    if part['type'] == 5:
        return math.hypot(a, b)
    return math.sqrt(a*a+b*b+c*c)
