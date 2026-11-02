"""Verify transport identity before treating payloads as evidence."""
import hashlib
import hmac
import json
import time


class Unauthorized(ValueError):
    pass


def signature(secret, body):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(source, headers, body, secret, now=None):
    if not secret:
        raise Unauthorized('Source is not connected')
    if source == 'slack':
        ts = headers.get('X-Slack-Request-Timestamp', '')
        try:
            if abs((time.time() if now is None else now) - int(ts)) > 300:
                raise Unauthorized('Expired Slack delivery')
        except ValueError as e:
            raise Unauthorized('Invalid Slack timestamp') from e
        expected = 'v0=' + signature(secret, b'v0:' + ts.encode() + b':' + body)
        supplied = headers.get('X-Slack-Signature', '')
    elif source == 'github':
        expected = 'sha256=' + signature(secret, body)
        supplied = headers.get('X-Hub-Signature-256', '')
    elif source == 'clickup':
        expected = signature(secret, body)
        supplied = headers.get('X-Signature', '')
    else:
        raise Unauthorized('Unsupported webhook source')
    if not hmac.compare_digest(expected, supplied):
        raise Unauthorized('Invalid webhook signature')


def normalize(source, payload, headers=None):
    headers = headers or {}
    if not isinstance(payload, dict):
        raise ValueError('Expected JSON object')
    if source == 'local':
        for key in ('id', 'title', 'text'):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ValueError(f'Missing {key}')
        if 'correlation' in payload and (not isinstance(payload['correlation'], str) or not 0 < len(payload['correlation']) <= 512):
            raise ValueError('Invalid correlation')
        if len(payload['id']) > 512:
            raise ValueError('Event ID too long')
        return {"source": source, "id": payload['id'], "correlation": payload.get('correlation') or payload['id'],
                "title": payload['title'], "text": payload['text'], "context": {}}
    if source == 'slack':
        e = payload.get('event', {})
        if payload.get('type') != 'event_callback' or e.get('type') not in {'message', 'app_mention'}:
            return None
        if e.get('bot_id') or e.get('subtype'):
            return None
        if not all(payload.get(k) for k in ('event_id', 'team_id')) or not all(e.get(k) for k in ('channel', 'user', 'ts')):
            raise ValueError('Incomplete Slack event')
        return {"source": source, "id": payload['event_id'],
                "correlation": f"{payload['team_id']}:{e['channel']}:{e.get('thread_ts', e['ts'])}",
                "title": 'Slack work request', "text": e.get('text', ''),
                "context": {"channel": e['channel'], "sender": e['user'], "team": payload['team_id'], "message_ts": e['ts']}}
    if source == 'github':
        kind = headers.get('X-GitHub-Event', '')
        if kind not in {'pull_request', 'pull_request_review', 'pull_request_review_comment', 'check_run', 'check_suite'}:
            return None
        repo = payload.get('repository', {}).get('full_name')
        delivery = headers.get('X-GitHub-Delivery')
        if not repo or not delivery:
            raise ValueError('Incomplete GitHub delivery')
        pr = payload.get('pull_request') or {}
        check = payload.get('check_run') or payload.get('check_suite') or {}
        prs = check.get('pull_requests', [])
        number = pr.get('number') or payload.get('number') or (prs[0].get('number') if prs else None)
        if not number:
            return None  # only PR-associated work, not every repository build
        return {"source": source, "id": delivery, "correlation": f'{repo}:pr:{number}',
                "title": f'{repo} PR #{number}: {kind}', "text": json.dumps(payload),
                "context": {"repository": repo, "pr": number, "head": pr.get('head', {}).get('sha') or check.get('head_sha')}}
    if source == 'clickup':
        if not payload.get('task_id') or not payload.get('webhook_id'):
            return None
        history = payload.get('history_items') or []
        # Assignment evidence must be explicit; never infer owner from arbitrary prose.
        assigned = []
        for h in history:
            if h.get('field') == 'assignee':
                after = h.get('after')
                values = after if isinstance(after, list) else [after]
                assigned.extend(str(v.get('id')) if isinstance(v, dict) else str(v) for v in values if v is not None)
        eid = payload['webhook_id'] + ':' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"source": source, "id": eid, "correlation": str(payload['task_id']),
                "title": f"ClickUp task {payload['task_id']}", "text": json.dumps(payload),
                "context": {"assignees": assigned, "task_id": str(payload['task_id'])}}
    raise ValueError('Unsupported source')


def select_responsibility(config, event):
    for rule in config['responsibilities']:
        if not rule.get('enabled') or rule['source'] != event['source']:
            continue
        c = event['context']
        if event['source'] == 'slack' and (c.get('channel') not in rule.get('channels', []) or c.get('sender') not in rule.get('senders', [])):
            continue
        if event['source'] == 'github' and c.get('repository') not in rule.get('repositories', []):
            continue
        if event['source'] == 'clickup' and str(rule.get('assignee_id')) not in c.get('assignees', []):
            continue
        return rule
    return None
