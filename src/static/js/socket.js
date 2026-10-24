// WebSocket session: token in the subprotocol header (never in the URL), heartbeat,
// exponential-backoff reconnect, and routing of server events.
const POLICY_VIOLATION = 1008;

const socket = {
    ws: null,
    attempts: 0,
    heartbeat: null,
    reconnectTimer: null,

    connect() {
        if (!state.token) return;
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${proto}//${location.host}/ws`, ["access_token", state.token]);

        this.ws.onopen = () => {
            this.attempts = 0;
            updateConnectionBadge(true);
            this.heartbeat = setInterval(() => this.send({ type: "ping" }), 20000);
        };
        this.ws.onmessage = (event) => {
            try {
                handleServerEvent(JSON.parse(event.data));
            } catch (err) {
                console.error("Bad server frame", err);
            }
        };
        this.ws.onclose = (event) => {
            updateConnectionBadge(false);
            clearInterval(this.heartbeat);
            if (event.code === POLICY_VIOLATION) {
                // Expired/invalid token: reconnecting would loop forever.
                handleLogout();
                return;
            }
            if (state.token) {
                this.attempts += 1;
                const delay = Math.min(1000 * 2 ** this.attempts, 30000);
                this.reconnectTimer = setTimeout(() => this.connect(), delay);
            }
        };
    },

    disconnect() {
        clearTimeout(this.reconnectTimer);
        clearInterval(this.heartbeat);
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
        updateConnectionBadge(false);
    },

    send(event) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(event));
            return true;
        }
        return false;
    },

    ack(messageId, status = "READ") {
        this.send({ type: "message_ack", data: { message_id: messageId, status } });
    },
};

function handleServerEvent({ type, data }) {
    switch (type) {
        case "message":
            if (data.room_id === state.activeRoomId) {
                renderMessage(data, true);
                if (data.sender_id !== state.currentUser.id) socket.ack(data.id, "READ");
            } else {
                setUnread(data.room_id, true);
                if (data.sender_id !== state.currentUser.id) socket.ack(data.id, "DELIVERED");
            }
            break;
        case "message_status_update":
            updateMessageStatus(data.message_id, data.status);
            break;
        case "joined_room":
        case "left_room":
            syncRooms();
            break;
        case "notification":
            showToast(`📢 ${data.title}`, data.body, data.level);
            break;
        case "error":
            showToast("Server", data.message, "warning");
            break;
        case "connected":
        case "pong":
        case "message_sent":
        case "presence_change":
            break;
        default:
            console.debug("Unhandled event", type, data);
    }
}
