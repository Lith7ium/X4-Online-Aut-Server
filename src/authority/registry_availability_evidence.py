"""Strict read-only verifier for a saved full registry availability snapshot.

Availability records binding presence; operational bits record the captured
operational observation. Both describe checkpoint bytes only. Operational bits
are not current command permission: callers must recheck live before acting.
"""
import hashlib
import re
import zlib
from xml.parsers import expat

from .checkpoints import Checkpoint
from .marker_evidence import _parse, MarkerEvidenceError, _PumpCallbackError


class RegistryAvailabilityEvidenceError(ValueError):
    """The saved availability snapshot is absent, ambiguous, or inconsistent."""


_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_REF = re.compile(r'[0-9]{1,32}\Z')
_COMPONENT = re.compile(r'\[0x([0-9a-f]+)\]\Z')
_INTEGER = re.compile(r'(?:0|[1-9][0-9]*)\Z')
_LARGE_INTEGER = re.compile(r'(?:0|[1-9][0-9]*)\.0+\Z')
_MAX = 200000
_FIELDS = {'$format', '$epoch', '$run_id', '$issued_count', '$cursor',
           '$available_count', '$unavailable_count', '$operational_count',
           '$sealed', '$faulted', '$available', '$operational', '$ships', '$byship'}
_CAPTURE_ONLY_FIELDS = {'$previous_run_id', '$previous_available', '$previous_ships', '$previous_byship'}


def _integer(field, expected=None):
    if field.get('type') != 'integer':
        raise RegistryAvailabilityEvidenceError('invalid integer field')
    raw = field.get('value')
    if raw is None:
        value = 0
    elif _INTEGER.fullmatch(raw):
        value = int(raw)
    else:
        raise RegistryAvailabilityEvidenceError('invalid integer value')
    if expected is not None and value != expected:
        raise RegistryAvailabilityEvidenceError('unexpected integer value')
    return value


def _table_index(kind, raw):
    if kind == 'integer' and _INTEGER.fullmatch(raw):
        value = int(raw)
    elif kind == 'largefloat' and (_INTEGER.fullmatch(raw) or _LARGE_INTEGER.fullmatch(raw)):
        value = int(raw.split('.', 1)[0]) if '.' in raw else int(raw)
    else:
        raise RegistryAvailabilityEvidenceError('invalid table index')
    if not 1 <= value <= _MAX:
        raise RegistryAvailabilityEvidenceError('table index outside supported range')
    return value


def verify(checkpoint, expected_epoch, expected_run_id, expected_count, *,
           verify_source_registry=False, pump=None):
    """Verify a snapshot; optionally cross-check the saved source registry.

    The opt-in source check relates saved registry bindings to the snapshot in
    these bytes. Operational bits remain captured observations, not live permission.
    """
    return _verify(checkpoint, expected_epoch, expected_run_id, expected_count,
                   verify_source_registry=verify_source_registry, pump=pump)


def verify_join_snapshot(checkpoint, *, describe_indices=(), pump=None):
    """Verify format-3 saved bytes for admission using the checkpoint's run marker.

    Epoch and issued count are discovered from the saved snapshot and checked
    against the source registry. The MD checkpoint marker is a separate check
    owned by the caller; this function only correlates the snapshot run ID.
    Explicit indices can include saved component and sector descriptions.
    """
    if (not isinstance(checkpoint, Checkpoint) or not isinstance(checkpoint.marker, str)
            or not _HEX32.fullmatch(checkpoint.marker)):
        raise RegistryAvailabilityEvidenceError('join checkpoint requires a run marker')
    if (type(describe_indices) is not tuple or len(describe_indices) > 32 or
            any(type(index) is not int or not 1 <= index <= _MAX for index in describe_indices) or
            len(set(describe_indices)) != len(describe_indices)):
        raise RegistryAvailabilityEvidenceError('invalid described registry indices')
    return _verify(checkpoint, None, checkpoint.marker, None,
                   verify_source_registry=True, pump=pump, discover=True,
                   describe_indices=describe_indices)


def _verify(checkpoint, expected_epoch, expected_run_id, expected_count, *,
            verify_source_registry, pump, discover=False, describe_indices=()):
    if (type(verify_source_registry) is not bool or not isinstance(checkpoint, Checkpoint)
            or (discover and verify_source_registry is not True)
            or (not discover and (not isinstance(expected_epoch, str)
                                 or not _HEX32.fullmatch(expected_epoch)
                                 or type(expected_count) is not int
                                 or not 1 <= expected_count <= _MAX))
            or not isinstance(expected_run_id, str) or not _HEX32.fullmatch(expected_run_id)):
        raise RegistryAvailabilityEvidenceError('invalid verification request')
    try:
        scripts = cues = var_sets = 0
        source_scripts = source_cues = source_var_sets = 0
        active_script = active_cue = active_source = active_source_cue = False
        fields = {}
        source_fields = {}

        def state(path, attrs):
            nonlocal scripts, cues, var_sets, source_scripts, source_cues, source_var_sets
            nonlocal active_script, active_cue, active_source, active_source_cue
            if path == ['savegame', 'md', 'script']:
                active_script = attrs.get('name') == 'X4AuthorityRegistryAvailabilitySnapshot'
                active_source = verify_source_registry and attrs.get('name') == 'X4AuthorityShipRegistry'
                active_cue = active_source_cue = False
                scripts += int(active_script)
                source_scripts += int(active_source)
            elif path == ['savegame', 'md', 'script', 'cue']:
                active_cue = active_script and attrs.get('name') == 'State'
                active_source_cue = active_source and attrs.get('name') == 'State'
                cues += int(active_cue)
                source_cues += int(active_source_cue)
            elif path == ['savegame', 'md', 'script', 'cue', 'vars']:
                if active_script and active_cue:
                    var_sets += 1
                if active_source and active_source_cue:
                    source_var_sets += 1
            elif path == ['savegame', 'md', 'script', 'cue', 'vars', 'value']:
                name = attrs.get('name')
                if active_script and active_cue and name in _CAPTURE_ONLY_FIELDS:
                    # BeginNext cannot start from a save that still carries prior capture state.
                    raise RegistryAvailabilityEvidenceError('saved snapshot contains unfinished capture state')
                if active_script and active_cue and name in _FIELDS:
                    if name in fields:
                        raise RegistryAvailabilityEvidenceError('duplicate snapshot field')
                    fields[name] = dict(attrs)
                if active_source and active_source_cue and name in ('$epoch', '$last', '$faulted', '$ships', '$byship'):
                    if name in source_fields:
                        raise RegistryAvailabilityEvidenceError('duplicate source registry field')
                    source_fields[name] = dict(attrs)

        _parse(checkpoint, state, pump=pump)
        if (scripts, cues, var_sets) != (1, 1, 1) or set(fields) != _FIELDS:
            raise RegistryAvailabilityEvidenceError('missing or ambiguous snapshot state')
        if verify_source_registry and (
                (source_scripts, source_cues, source_var_sets) != (1, 1, 1)
                or set(source_fields) != {'$epoch', '$last', '$faulted', '$ships', '$byship'}):
            raise RegistryAvailabilityEvidenceError('missing or ambiguous source registry state')
        if discover:
            expected_count = _integer(fields['$issued_count'])
            if not 1 <= expected_count <= _MAX:
                raise RegistryAvailabilityEvidenceError('issued count outside supported range')
        if verify_source_registry:
            source_epoch = source_fields['$epoch']
            if source_epoch.get('type') != 'string' or not _REF.fullmatch(source_epoch.get('value', '')):
                raise RegistryAvailabilityEvidenceError('invalid source registry epoch reference')
            _integer(source_fields['$last'], expected_count)
            if source_fields['$faulted'].get('type') != 'integer' or 'value' in source_fields['$faulted']:
                raise RegistryAvailabilityEvidenceError('source registry faulted flag is not confirmed false')
        _integer(fields['$format'], 3)
        for name, wanted in (('$epoch', expected_epoch), ('$run_id', expected_run_id)):
            field = fields[name]
            if field.get('type') != 'string' or not _REF.fullmatch(field.get('value', '')):
                raise RegistryAvailabilityEvidenceError('invalid correlation reference')
        if fields['$epoch']['value'] == fields['$run_id']['value']:
            raise RegistryAvailabilityEvidenceError('correlation strings alias')
        _integer(fields['$issued_count'], expected_count)
        _integer(fields['$cursor'], expected_count)
        available_count = _integer(fields['$available_count'])
        unavailable_count = _integer(fields['$unavailable_count'])
        operational_count = _integer(fields['$operational_count'])
        if available_count + unavailable_count != expected_count:
            raise RegistryAvailabilityEvidenceError('availability counters disagree')
        if operational_count > available_count:
            raise RegistryAvailabilityEvidenceError('operational count exceeds available bindings')
        _integer(fields['$sealed'], 1)
        if fields['$faulted'].get('type') != 'integer' or 'value' in fields['$faulted']:
            raise RegistryAvailabilityEvidenceError('saved faulted flag is not confirmed false')

        table_names = ('$available', '$operational', '$ships', '$byship')
        table_refs = {}
        for name in table_names:
            field = fields[name]
            if field.get('type') != 'table' or not _REF.fullmatch(field.get('value', '')):
                raise RegistryAvailabilityEvidenceError('invalid table reference')
            table_refs[name] = field['value']
        if verify_source_registry:
            for name, source_name in (('$ships', '$ships'), ('$byship', '$byship')):
                field = source_fields[source_name]
                if field.get('type') != 'table' or not _REF.fullmatch(field.get('value', '')):
                    raise RegistryAvailabilityEvidenceError('invalid source registry table reference')
                table_refs['$source_' + name[1:]] = field['value']
        if len(set(table_refs.values())) != len(table_refs):
            raise RegistryAvailabilityEvidenceError('snapshot/source registry tables alias')

        selected = {('table', ref): name for name, ref in table_refs.items()}
        string_refs = {('string', fields['$epoch']['value']): ['$epoch'],
                       ('string', fields['$run_id']['value']): ['$run_id']}
        if verify_source_registry:
            source_epoch_ref = source_fields['$epoch']['value']
            if source_epoch_ref in (fields['$epoch']['value'], fields['$run_id']['value']):
                raise RegistryAvailabilityEvidenceError('source epoch aliases snapshot correlation string')
            string_refs.setdefault(('string', source_epoch_ref), []).append('$source_epoch')
        selected.update({key: names[0] for key, names in string_refs.items()})
        selected_pairs = set(selected)
        seen = set()
        availability = {}
        operational = {}
        ships = {}
        ship_components = set()
        reverse = {}
        source_forward = {}
        source_reverse = {}
        source_components = set()
        source_null_indices = set()
        resolved = {'$epoch': [], '$run_id': [], '$source_epoch': []}
        active_group = active_ref = None
        pending = None

        def refs(path, attrs):
            nonlocal active_group, active_ref, pending
            if path == ['savegame', 'script', 'refs']:
                if active_ref and pending is not None:
                    raise RegistryAvailabilityEvidenceError('dangling selected table key')
                active_group, active_ref, pending = attrs.get('type'), None, None
            elif path == ['savegame', 'script', 'refs', 'ref']:
                if active_ref and pending is not None:
                    raise RegistryAvailabilityEvidenceError('unpaired selected table key')
                active_ref = None
                typed = (active_group, attrs.get('id'))
                if typed not in selected:
                    return
                if typed in seen:
                    raise RegistryAvailabilityEvidenceError('duplicate selected reference')
                seen.add(typed)
                name = selected[typed]
                if active_group == 'table':
                    active_ref = name
                else:
                    for resolved_name in string_refs[typed]:
                        resolved[resolved_name].append(attrs.get('string'))
            elif len(path) == 5 and path[:4] == ['savegame', 'script', 'refs', 'ref'] and active_ref:
                tag, kind, raw = path[-1], attrs.get('type'), attrs.get('value', '')
                if tag == 'key':
                    if pending is not None:
                        raise RegistryAvailabilityEvidenceError('duplicate or unpaired table key')
                    if active_ref in ('$available', '$operational'):
                        pending = _table_index(kind, raw)
                    elif active_ref in ('$ships', '$source_ships'):
                        if active_ref == '$source_ships':
                            if kind != 'integer' or not _INTEGER.fullmatch(raw):
                                raise RegistryAvailabilityEvidenceError('invalid source forward index')
                            pending = ('index', int(raw))
                        else:
                            pending = ('index', _table_index(kind, raw))
                    else:
                        if kind != 'component' or not _COMPONENT.fullmatch(raw):
                            raise RegistryAvailabilityEvidenceError('invalid reverse component key')
                        component = _source_component(raw) if active_ref == '$source_byship' else _component(raw)
                        pending = ('component', component)
                elif tag == 'value':
                    if pending is None:
                        raise RegistryAvailabilityEvidenceError('table value without key')
                    key, pending = pending, None
                    if active_ref in ('$available', '$operational'):
                        if kind != 'integer' or raw not in ('', '0', '1'):
                            raise RegistryAvailabilityEvidenceError('invalid availability/operational bit')
                        table = availability if active_ref == '$available' else operational
                        if key in table:
                            raise RegistryAvailabilityEvidenceError('duplicate availability/operational index')
                        table[key] = 0 if raw == '' else int(raw)
                    elif active_ref == '$ships':
                        if kind != 'component' or not _COMPONENT.fullmatch(raw):
                            raise RegistryAvailabilityEvidenceError('invalid ship component value')
                        component = _component(raw)
                        if key[1] in ships or component in ship_components:
                            raise RegistryAvailabilityEvidenceError('duplicate ship mapping')
                        ships[key[1]] = component
                        ship_components.add(component)
                    elif active_ref == '$source_ships':
                        if kind != 'component' or not _COMPONENT.fullmatch(raw):
                            raise RegistryAvailabilityEvidenceError('invalid source component value')
                        component = _source_component(raw)
                        index = key[1]
                        if index in source_forward or not 1 <= index <= expected_count:
                            raise RegistryAvailabilityEvidenceError('duplicate or invalid source forward index')
                        if component == '0':
                            source_null_indices.add(index)
                        elif component in source_components:
                            raise RegistryAvailabilityEvidenceError('duplicate source component binding')
                        else:
                            source_components.add(component)
                        source_forward[index] = component
                    elif active_ref == '$source_byship':
                        if kind != 'integer' or not _INTEGER.fullmatch(raw):
                            raise RegistryAvailabilityEvidenceError('invalid source reverse index')
                        index = int(raw)
                        if not 1 <= index <= expected_count or key[1] in source_reverse:
                            raise RegistryAvailabilityEvidenceError('duplicate or invalid source reverse mapping')
                        source_reverse[key[1]] = index
                    else:
                        if kind != 'integer' or not _INTEGER.fullmatch(raw):
                            raise RegistryAvailabilityEvidenceError('invalid reverse index')
                        index = int(raw)
                        if not 1 <= index <= expected_count or key[1] in reverse:
                            raise RegistryAvailabilityEvidenceError('duplicate or invalid reverse mapping')
                        reverse[key[1]] = index
                else:
                    raise RegistryAvailabilityEvidenceError('unexpected selected table child')
            elif len(path) > 5 and path[:4] == ['savegame', 'script', 'refs', 'ref'] and active_ref:
                raise RegistryAvailabilityEvidenceError('nested selected table entry')

        _parse(checkpoint, refs, pump=pump)
        if pending is not None or seen != selected_pairs:
            raise RegistryAvailabilityEvidenceError('missing or incomplete selected references')
        if discover:
            if (len(resolved['$epoch']) != 1 or not isinstance(resolved['$epoch'][0], str)
                    or not _HEX32.fullmatch(resolved['$epoch'][0])):
                raise RegistryAvailabilityEvidenceError('invalid saved snapshot epoch')
            expected_epoch = resolved['$epoch'][0]
        if (expected_epoch == expected_run_id or resolved['$epoch'] != [expected_epoch]
                or resolved['$run_id'] != [expected_run_id]):
            raise RegistryAvailabilityEvidenceError('correlation strings mismatch')
        if verify_source_registry:
            if resolved['$source_epoch'] != [expected_epoch]:
                raise RegistryAvailabilityEvidenceError('source registry epoch mismatch')
            if set(source_forward) != set(range(1, expected_count + 1)):
                raise RegistryAvailabilityEvidenceError('source forward table has gaps or extra indices')
            nonnull_source = {i: component for i, component in source_forward.items() if component != '0'}
            for index, component in nonnull_source.items():
                if source_reverse.get(component) != index:
                    raise RegistryAvailabilityEvidenceError('source forward/reverse mismatch')
            if len(set(source_reverse.values())) != len(source_reverse):
                raise RegistryAvailabilityEvidenceError('source reverse indices are not unique')
            for component, index in source_reverse.items():
                if component == '0':
                    if index not in source_null_indices:
                        raise RegistryAvailabilityEvidenceError('source null reverse points to nonnull binding')
                elif source_forward.get(index) != component:
                    raise RegistryAvailabilityEvidenceError('source reverse has extra or mismatched binding')
            if len(source_reverse) != len(nonnull_source) + int('0' in source_reverse):
                raise RegistryAvailabilityEvidenceError('source reverse table is incomplete')
        expected_indices = set(range(1, expected_count + 1))
        if set(availability) != expected_indices:
            raise RegistryAvailabilityEvidenceError('availability table has gaps or extra indices')
        if set(operational) != expected_indices:
            raise RegistryAvailabilityEvidenceError('operational table has gaps or extra indices')
        available_indices = {i for i, bit in availability.items() if bit == 1}
        operational_indices = {i for i, bit in operational.items() if bit == 1}
        if len(available_indices) != available_count or expected_count - len(available_indices) != unavailable_count:
            raise RegistryAvailabilityEvidenceError('availability bits disagree with counters')
        if len(operational_indices) != operational_count or operational_indices - available_indices:
            raise RegistryAvailabilityEvidenceError('operational bits disagree with binding availability or count')
        if set(ships) != available_indices or set(reverse.values()) != available_indices:
            raise RegistryAvailabilityEvidenceError('ship tables do not match available indices')
        if len(reverse) != len(ships) or any(reverse.get(component) != index for index, component in ships.items()):
            raise RegistryAvailabilityEvidenceError('ship tables are not exact inverses')

        if verify_source_registry:
            for index in available_indices:
                if source_forward[index] != ships[index] or source_reverse.get(ships[index]) != index:
                    raise RegistryAvailabilityEvidenceError('snapshot binding differs from source registry')
            unavailable_source_components = {source_forward[i] for i in range(1, expected_count + 1)
                                             if i not in available_indices and source_forward[i] != '0'}
        else:
            unavailable_source_components = set()
        if any(index not in operational_indices for index in describe_indices):
            raise RegistryAvailabilityEvidenceError('described ship is unavailable or inoperable')
        selected_indices = {ships[index]: index for index in describe_indices}
        descriptions = {}
        ancestors = []
        wanted = set(ships.values())
        found = set()
        unavailable_present = set()
        def components(path, attrs):
            if selected_indices:
                del ancestors[len(path) - 1:]
                ancestors.append((attrs.get('class'), attrs.get('macro'))
                                 if path[-1] == 'component' else None)
            if len(path) < 3 or path[:2] != ['savegame', 'universe'] or path[-1] != 'component':
                return
            match = _COMPONENT.fullmatch(attrs.get('id', ''))
            if not match:
                return
            component = _component(attrs['id'])
            if component in unavailable_source_components:
                unavailable_present.add(component)
            if component not in wanted:
                return
            if component in found:
                raise RegistryAvailabilityEvidenceError('duplicate saved component definition')
            if not attrs.get('class', '').startswith('ship_'):
                raise RegistryAvailabilityEvidenceError('available component is not a ship')
            found.add(component)
            if component in selected_indices:
                parents = [parent for parent in ancestors[:-1] if parent is not None]
                sectors = [macro for kind, macro in parents if kind == 'sector']
                descriptions[selected_indices[component]] = {
                    'id': component, 'macro': attrs.get('macro'), 'class': attrs['class'],
                    'owner': attrs.get('owner'), 'code': attrs.get('code'),
                    'connection': attrs.get('connection'),
                    'parent_class': parents[-1][0] if parents else None,
                    'sector': sectors[0] if len(sectors) == 1 else None,
                }

        _parse(checkpoint, components, pump=pump)
        if found != wanted:
            raise RegistryAvailabilityEvidenceError('missing saved ship component definition')
        if unavailable_present:
            raise RegistryAvailabilityEvidenceError('unavailable source binding has a saved component definition')
        bits = ''.join(str(availability[i]) for i in range(1, expected_count + 1))
        operational_bits = ''.join(str(operational[i]) for i in range(1, expected_count + 1))
        digest = hashlib.sha256(b'X4AuthorityRegistryAvailabilitySnapshot mapping v3\n')
        for index in sorted(ships):
            digest.update(f'{index}:{ships[index]}\n'.encode('ascii'))
        availability_digest = hashlib.sha256(b'X4AuthorityRegistryAvailabilitySnapshot availability v3\n')
        availability_digest.update(bits.encode('ascii'))
        operational_digest = hashlib.sha256(b'X4AuthorityRegistryAvailabilitySnapshot operational v3\n')
        operational_digest.update(operational_bits.encode('ascii'))
        result = {'evidence_scope': 'saved checkpoint bytes only', 'format': 3,
                'operational_scope': 'captured saved observation only; commands must recheck live before acting',
                'epoch': expected_epoch, 'run_id': expected_run_id,
                'issued_count': expected_count, 'available_count': available_count,
                'unavailable_count': unavailable_count, 'availability_bits': bits,
                'operational_count': operational_count, 'operational_bits': operational_bits,
                'mapping_sha256': digest.hexdigest(),
                'availability_sha256': availability_digest.hexdigest(),
                'operational_sha256': operational_digest.hexdigest(),
                'source_registry_checked': verify_source_registry}
        if describe_indices:
            result['described_ships'] = descriptions
        return result
    except _PumpCallbackError as error:
        raise error.error
    except RegistryAvailabilityEvidenceError:
        raise
    except (MarkerEvidenceError, OSError, EOFError, expat.ExpatError, zlib.error) as error:
        raise RegistryAvailabilityEvidenceError('snapshot checkpoint could not be verified') from error


def _source_component(raw):
    match = _COMPONENT.fullmatch(raw)
    return format(int(match.group(1), 16), 'x')


def _component(raw):
    component = _source_component(raw)
    if component == '0':
        raise RegistryAvailabilityEvidenceError('null component reference')
    return component
