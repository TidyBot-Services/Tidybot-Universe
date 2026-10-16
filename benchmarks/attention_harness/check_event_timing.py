"""Offline timing audit of recorded Dev v2 preview executions."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from .dev_v2_loop import events_for_moment_selection
from .moment_localization import select_moments

EXAMPLES = {
    ('devexp-8a2458597ffd719d33d9c22f', 'v1'),
    ('devexp-1c6390de0bd4651a77d0cfa0', 'v1'),
    ('devexp-c7dfce4767857ec8f8291b65', 'v2'),
}


def audit(preview_data, output):
    preview_data, output = Path(preview_data), Path(output)
    files = sorted((preview_data / 'daily-experiments').glob('*/artifacts/*/cells/*/v*/public-feedback.json'))
    output.mkdir(parents=True, exist_ok=True)
    summary, rows, exports = Counter(), [], []
    for feedback_file in files:
        version_dir = feedback_file.parent
        trace = json.loads(feedback_file.read_text())['action_trace']
        failed = [i for i, event in enumerate(trace) if event.get('status') == 'failed']
        execution_dirs = list((version_dir / 'executions').glob('*'))
        selected = []
        if len(execution_dirs) != 1:
            localization = {'status': 'generic_fallback', 'reason': 'execution_directory_missing_or_ambiguous'}
        elif not failed:
            localization = {'status': 'generic_fallback', 'reason': 'no_failed_event'}
        else:
            execution = execution_dirs[0]
            events = events_for_moment_selection(execution, trace, int(version_dir.name[1:]))
            try:
                timing = json.loads((execution / 'camera_recording.json').read_text())['replay_timing']
            except (OSError, ValueError, KeyError, TypeError):
                timing = None
            selected, localization = select_moments(
                events, [events[i]['event_id'] for i in failed], timing,
                available_views=('external',), max_frames=4)
            if localization['status'] == 'targeted':
                frame_file = execution / 'recording_frames' / 'frames.jsonl'
                try:
                    frame_rows = [json.loads(line) for line in frame_file.read_text().splitlines() if line.strip()]
                    for moment in selected:
                        name = frame_rows[moment['frame_index']]['file']
                        path = frame_file.parent / name
                        if Path(name).name != name or path.is_symlink() or not path.is_file():
                            raise ValueError('missing image')
                except (OSError, ValueError, IndexError, KeyError, TypeError):
                    selected, localization = [], {'status': 'generic_fallback', 'reason': 'selected_frame_missing'}
        label = str(version_dir.relative_to(preview_data / 'daily-experiments'))
        summary[localization['status'] if localization['status'] == 'targeted' else localization['reason']] += 1
        rows.append({'version': label, 'localization': localization, 'selected_moments': selected})
        if localization['status'] == 'targeted' and (label.split('/')[0], version_dir.name) in EXAMPLES:
            for moment in selected:
                event = trace[int(moment['event_id'].split(':')[1])]
                source = execution / 'recording_frames' / frame_rows[moment['frame_index']]['file']
                filename = f"{label.split('/')[0]}-{version_dir.name}-{moment['role']}-frame{moment['frame_index']:03d}.jpg"
                shutil.copyfile(source, output / filename)
                exports.append({'file': filename, 'version': label, 'operation': event['operation'],
                                'status': event['status'], 'view': moment['view'], 'role': moment['role'],
                                'frame_index': moment['frame_index'], 'video_seconds': moment['video_seconds']})
    result = {'versions': len(files), 'summary': dict(sorted(summary.items())), 'versions_detail': rows, 'exports': exports}
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    lines = ['# Dev v2 事件时间离线检查', '',
             '只读扫描 preview-data/daily-experiments 的公开反馈及执行 journal；选择每个版本所有 status=failed 的公开事件，用仅供选帧的推导时间调用 select_moments。视图限定 external，最多 4 帧。没有运行模型或模拟器。', '',
             f"版本总数：{len(files)}。成功定位：{summary['targeted']}。回退：{len(files) - summary['targeted']}。", '',
             '回退原因：']
    lines.extend(f'- `{reason}`：{count}' for reason, count in sorted(summary.items()) if reason != 'targeted')
    lines.extend(['', '## 导出的原始 JPEG', '', '图片仅供人工查看，不对帧内容是否合适作判断。', '',
                  '| 图片 | 版本 | 事件 | 视图 | 时点 | 帧序号 | 视频秒 |',
                  '| --- | --- | --- | --- | --- | ---: | ---: |'])
    for item in exports:
        lines.append(f"| [{item['file']}]({item['file']}) | `{item['version']}` | "
                     f"`{item['operation']}/{item['status']}` | {item['view']} | {item['role']} | "
                     f"{item['frame_index']} | {item['video_seconds']:.3f} |")
    lines.extend(['', '完整逐版本定位结果见 `summary.json`。', ''])
    (output / 'README.md').write_text('\n'.join(lines))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.preview_data, args.output)
    print(json.dumps({'versions': result['versions'], 'summary': result['summary'],
                      'exports': len(result['exports'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
