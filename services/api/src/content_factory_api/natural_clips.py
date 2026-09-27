"""Speech-boundary candidates. Timing evidence is local, never model-authored.

Sentence punctuation and observed pauses propose safe speech edges. They do not
prove visual action completion or semantic independence; those remain reviewed
by the multimodal planner and the human reviewing the output.
"""
import re


def build_clip_pool(words, *, source_duration_ms, minimum, maximum, raw_segments=()):
    units, current = [], []
    # ASR often emits unpunctuated Chinese. Preserve its observed phrase edges,
    # but only when the edge is also an actual acoustic word end. These are
    # candidates, not a claim of semantic independence; the planner reviews text.
    phrase_ends = {s['end_ms'] for s in raw_segments
                   if type(s.get('end_ms')) is int and 0 < s['end_ms'] <= source_duration_ms}
    previous_end = 0
    for index, word in enumerate(words):
        start, end = word.get('start_ms'), word.get('end_ms')
        if type(start) is not int or type(end) is not int or not previous_end <= start < end <= source_duration_ms:
            raise ValueError('原声字词时间无效，不能建立自然片段')
        previous_end = end
        current.append(word)
        next_start = words[index + 1]['start_ms'] if index + 1 < len(words) else source_duration_ms
        punctuation = bool(re.search(r'[。！？!?][”’"\u300d]*$', word.get('text', '')))
        pause = next_start - end >= 500 and word.get('break_after') is not False
        if punctuation or pause or end in phrase_ends:
            units.append({'start_ms': current[0]['start_ms'], 'end_ms': end,
                          'text': ''.join(w['text'] for w in current)})
            current = []
    # A cut-off final utterance without punctuation/pause is not a complete unit.
    pool = []
    ceiling = maximum - 200  # Leave headroom for one output frame/AAC packet.
    for index, first in enumerate(units):
        text = ''
        for last in units[index:]:
            text += last['text']
            duration = last['end_ms'] - first['start_ms']
            if duration > ceiling:
                break
            if duration >= 200:
                pool.append({'start_ms': first['start_ms'], 'end_ms': last['end_ms'], 'text': text})
    # Keep evidence bounded and distributed throughout long recordings.
    if len(pool) > 240:
        pool = [pool[round(i * (len(pool) - 1) / 239)] for i in range(240)]
    return pool


def build_visual_clip_pool(shots, *, source_duration_ms, minimum, maximum, allow_fixed_fallback=False):
    """Build complete-shot candidates for projects that deliberately disable ASR.

    The source audio is still copied by the renderer.  These candidates only
    use boundaries produced by the local shot detector, so the planner cannot
    invent a cut in the middle of a detected shot.  They do not claim that a
    spoken sentence ends at the same point; that guarantee is reserved for the
    ASR-backed subtitle modes.
    """
    boundaries = []
    for shot in shots or ():
        if not isinstance(shot, dict):
            continue
        start, end = shot.get('start_ms'), shot.get('end_ms')
        if type(start) is not int or type(end) is not int:
            continue
        if not 0 <= start < end <= source_duration_ms:
            continue
        if boundaries and start < boundaries[-1]:
            continue
        boundaries.extend([start, end])
    boundaries = sorted(set(boundaries))
    if len(boundaries) < 2:
        boundaries = []
    pool = []
    ceiling = maximum - 200
    for start_index, start in enumerate(boundaries[:-1]):
        for end in boundaries[start_index + 1:]:
            duration = end - start
            if duration > ceiling:
                break
            if duration >= 200:
                pool.append({'start_ms': start, 'end_ms': end, 'boundary_kind': 'visual'})
    if not pool and allow_fixed_fallback and source_duration_ms >= minimum:
        # A continuous talking-head recording can legitimately be one long
        # detected shot.  Preserve the audio and make safe hard-duration
        # windows rather than treating the missing scene cut as an ASR error.
        target = min(maximum - 200, max(minimum, 20_000))
        if target <= 0:
            return []
        for start in range(0, max(1, source_duration_ms - target + 1), target):
            end = min(source_duration_ms, start + target)
            if end - start >= minimum and end - start <= maximum - 200:
                pool.append({'start_ms': start, 'end_ms': end, 'boundary_kind': 'fixed_visual_fallback'})
        if not pool and source_duration_ms >= minimum:
            end = min(source_duration_ms, maximum - 200)
            if end >= minimum:
                pool.append({'start_ms': 0, 'end_ms': end, 'boundary_kind': 'fixed_visual_fallback'})
    if len(pool) > 240:
        pool = [pool[round(i * (len(pool) - 1) / 239)] for i in range(240)]
    return pool


def pool_can_fit(pool, *, minimum, maximum, max_pieces=30):
    """Repeated complete clips are allowed; each occurrence consumes time."""
    lengths = {p['end_ms'] - p['start_ms'] for p in pool}
    reachable = {0}
    for _ in range(max_pieces):
        reachable = {total + length for total in reachable for length in lengths
                     if total + length <= maximum - 200}
        if any(minimum + 100 <= total for total in reachable):
            return True
        if not reachable:
            break
    return False


def attach_natural_evidence(candidate, pool, *, boundary_kind='speech'):
    allowed = {(p['start_ms'], p['end_ms']) for p in pool}
    clips = candidate.get('clips')
    if not isinstance(clips, list) or not clips:
        raise ValueError('模型没有选择自然片段')
    if any((c.get('start_ms'), c.get('end_ms')) not in allowed for c in clips):
        raise ValueError('模型选段未使用已核对的自然片段边界，请修改条件后重新规划')
    candidate['natural_clip_evidence'] = {
        'version': 1, 'boundary_kind': boundary_kind, 'clips': [
            {'start_ms': c['start_ms'], 'end_ms': c['end_ms']} for c in clips]}


def validate_natural_evidence(candidate):
    evidence = candidate.get('natural_clip_evidence') or {}
    if evidence.get('version') != 1 or evidence.get('clips') != candidate.get('clips'):
        raise ValueError('剪辑方案缺少匹配的自然片段边界证据，请重新规划')
