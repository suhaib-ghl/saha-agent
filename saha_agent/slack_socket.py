"""Optional Slack push transport. No public tunnel and no polling/model idle cost.

Requires an explicitly installed Slack app and owner-selected channels. Bot tokens
cannot read the owner's personal self-DM; communication is a DM with the bot.
"""
import json
from pathlib import Path


class SlackSocket:
    def __init__(self,runtime):
        self.runtime=runtime
        self.client=None

    def start(self):
        cfg=self.runtime.config.get('integrations',{}).get('slack',{})
        if not cfg.get('enabled'):return
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
        credentials=json.loads(Path(cfg['credentials_file']).expanduser().read_text())
        web=WebClient(token=credentials['bot_token'])
        identity=web.auth_test()
        if identity['team_id']!=cfg['team_id']:raise RuntimeError('Slack workspace identity mismatch')
        self.client=SocketModeClient(app_token=credentials['app_token'],web_client=web)
        def receive(client,request):
            if request.type=='events_api':
                # Durable commit before ACK. Failed ingestion is left for Slack retry.
                payload=request.payload
                if payload.get('team_id')!=cfg['team_id']:return
                self.runtime.ingest('slack',payload)
            client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        self.client.socket_mode_request_listeners.append(receive)
        self.client.connect()
        self.runtime.connections.record('slack','connected','Authenticated Socket Mode listener',event_transport='websocket')

    def close(self):
        if self.client:self.client.close()
