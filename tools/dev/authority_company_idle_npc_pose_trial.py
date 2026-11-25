"""One opt-in pose write after the private WRN NPC undock trial."""
import math
import re
import time
import xml.etree.ElementTree as ET

import authority_company_pose_overlay as overlay
from authority_company_pose_trial import READBACK_METRES
import authority_company_undock_trial as undock

INDEX = 7354
SHIP = 'WRN-302'
SECTOR = 'cluster_01_sector001_macro'
STEP_METRES = 50
_RELEASE = 'md.X4AuthorityCompanyUndockTrial.State.$PoseRelease'


def _set(name, exact):
    return ET.Element('set_value', {'name': name, 'exact': exact})


def _gate(parent, ref, value):
    child = ET.Element('do_elseif', {'value': value})
    child.append(_set('$status', "'IDLE_NPC_TRIAL_FENCE'"))
    parent.insert(list(parent).index(ref), child)


def render_undock(data):
    """Expose only the already verified stock release to the private pose cue."""
    root = ET.fromstring(data)
    readback = root.find(".//cue[@name='Readback']/actions/do_if")
    statuses = readback.findall("set_value[@exact=\"'undocked'\"]")
    if len(statuses) != 1:
        raise ValueError('expected one proved berth departure')
    readback.append(_set('State.$PoseRelease',
                         'table[$ship=$Ship,$captain=$Captain,$avatar=$Avatar,$station=$AvatarStation,$room=$AvatarRoom]'))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def render_pose(data, generation):
    """Fence the ordinary stock pose implementation inside a disposable overlay."""
    if not re.fullmatch(r'[0-9a-f]{32}', generation):
        raise ValueError('invalid one-shot trial generation')
    root = ET.fromstring(data)
    request = root.find("./cues/cue[@name='Request']/actions/do_if")
    dispatch = request.find('do_elseif')
    _gate(request, dispatch,
          f"$index != {INDEX} or $args.$generation != '{generation}' or $args.$frame != '{SECTOR}' "
          "or ($action != 'inspect' and $action != 'apply' and $action != 'verify')")
    # The unchanged registry and avatar checks still execute at every read/write.
    check = root.find("./cues/library[@name='CheckShip']/actions/do_else")
    pilot = check.find("do_elseif[@value='$ship.pilot.exists or $ship.assignedpilot.exists']")
    pilot.set('value',
              f"not @{_RELEASE} or {_RELEASE}.$ship != $ship or $ship.idcode != '{SHIP}' "
              f"or $ship.trueowner != faction.player or not @{_RELEASE}.$captain.isclass.npc "
              f"or {_RELEASE}.$captain.owner != faction.player or {_RELEASE}.$captain.isbusy "
              f"or $ship.pilot != {_RELEASE}.$captain or $ship.assignedaipilot != {_RELEASE}.$captain "
              f"or {_RELEASE}.$captain.assignedcontrolled != $ship or {_RELEASE}.$captain.ship != $ship "
              f"or not {_RELEASE}.$captain.hascontext.{{$ship}} or $ship.orders.count != 0 "
              f"or $ship.tradeorders.count != 0 or @$ship.tradecomputer.exists "
              f"or not $ship.defaultorder or $ship.defaultorder.id != 'Wait' "
              f"or not $ship.order or $ship.order.id != 'Wait' "
              f"or $ship.order.state == orderstate.critical "
              f"or player.controlled or player.occupiedship or player.entity != {_RELEASE}.$avatar "
              f"or player.station != {_RELEASE}.$station or player.room != {_RELEASE}.$room")
    pilot.find('set_value').set('exact', "'IDLE_NPC_TRIAL_FENCE'")

    read = root.find("./cues/library[@name='ProcessPose']/actions/do_else")
    capture = ET.SubElement(read, 'do_if', {'value': "$action == 'inspect' and not State.$TrialBefore?"})
    capture.append(_set('State.$TrialBefore', 'table[$x=$x,$y=$y,$z=$z]'))

    apply = root.find("./cues/library[@name='ApplyPose']/actions/do_if")
    apply.set('value', apply.get('value') +
              ' and State.$TrialBefore? and not @State.$TrialWrite '
              f'and $args.$x ge State.$TrialBefore.$x + {STEP_METRES - .5} '
              f'and $args.$x le State.$TrialBefore.$x + {STEP_METRES + .5} '
              'and $args.$y ge State.$TrialBefore.$y - 0.5 and $args.$y le State.$TrialBefore.$y + 0.5 '
              'and $args.$z ge State.$TrialBefore.$z - 0.5 and $args.$z le State.$TrialBefore.$z + 0.5')
    # An uncertain dispatch also exhausts this trial's one write.
    apply.insert(0, _set('State.$TrialWrite', 'true'))
    ET.SubElement(request, 'debug_text', {
        'text': "'[X4LAN] AUTH_COMPANY_NPC_POSE %s|%s|%s|actor=%s|captain=%s|age_s=%s'."
                f"[$args.$request_id,$action,$status,$actor,@{_RELEASE}.$captain,player.age / 1s]",
        'chance': '100',
    })
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def install(case, epoch, nonce, channel, generation):
    undock_files = undock.install(case, 'inspect', epoch, nonce)
    pose_files = overlay.install(case, case / 'home/.config/EgoSoft/X4/45841',
                                 port=channel.port, lease=channel.lease, epoch=epoch, writable=True)
    undock_cue = undock_files[0]
    pose_cue = case / 'extensions/x4_lan_lab/md/company_ship_pose.xml'
    undock_cue.write_bytes(render_undock(undock_cue.read_bytes()))
    pose_cue.write_bytes(render_pose(pose_cue.read_bytes(), generation))
    return list(dict.fromkeys(undock_files + pose_files))


def verify_one_pose(channel, generation, check, *, report=None):
    """One write and two later separate MD/UI inspections on the owned socket."""
    if channel.ship_codes != {INDEX: SHIP} or channel.ship_sectors != {INDEX: SECTOR}:
        raise ValueError('reserve exactly the verified WRN seat for the private pose trial')
    if report is None:
        report = {}
    report.update(ship_index=INDEX, sector=SECTOR, writes_dispatched=0)
    sample = dict(index=INDEX, epoch=channel.epoch, generation=generation, sector_macro=SECTOR,
                  x=0, y=0, z=0)
    channel.begin('inspect', sample)
    before = channel.wait()
    x, y, z = before['md']
    target = (x + STEP_METRES, y, z)
    report.update(before=before, target=target)
    sample.update(zip('xyz', target))
    actor = before['actor']
    check()
    channel.begin('apply', sample, actor)
    channel.wait()
    report['writes_dispatched'] = 1

    def read():
        channel.begin('verify', sample, actor)
        reply = channel.wait()
        error = max(math.dist(target, reply['md']), math.dist(target, reply['ui']))
        return reply, error

    deadline = time.monotonic() + 4
    while True:
        early, early_error = read()
        report.update(early=early, early_error_m=early_error)
        if early_error <= READBACK_METRES:
            break
        if time.monotonic() >= deadline:
            raise ValueError(f'one-shot NPC pose did not reach target: {early_error:.3f} m')
    later = time.monotonic() + 2
    while time.monotonic() < later:
        check()
        time.sleep(.04)
    late, late_error = read()
    report.update(late=late, late_error_m=late_error)
    if late_error > READBACK_METRES:
        raise ValueError(f'one-shot NPC pose diverged on later readback: {late_error:.3f} m')
    return report
