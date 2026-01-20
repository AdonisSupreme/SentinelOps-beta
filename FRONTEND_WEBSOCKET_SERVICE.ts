// src/services/websocket.ts
/**
 * WebSocket Service for Real-Time Notifications
 * 
 * Handles:
 * - Connection management with automatic reconnection
 * - Authentication via JWT token
 * - Message handling and dispatching
 * - Event subscription pattern
 * - Graceful disconnect/reconnect
 */

export type WebSocketMessageType = 
  | 'ping' 
  | 'get_unread' 
  | 'mark_read';

export type ServerMessageType =
  | 'pong'
  | 'unread_notifications'
  | 'new_notification'
  | 'notification_updated'
  | 'error';

export interface WebSocketMessage {
  type: WebSocketMessageType;
  [key: string]: any;
}

export interface ServerMessage {
  type: ServerMessageType;
  [key: string]: any;
}

export interface NotificationEvent {
  type: 'new' | 'updated' | 'error' | 'connected' | 'disconnected';
  data?: any;
  error?: string;
}

interface SubscriberCallback {
  (event: NotificationEvent): void;
}

class WebSocketService {
  private ws: WebSocket | null = null;
  private url: string;
  private token: string | null = null;
  private subscribers: Set<SubscriberCallback> = new Set();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 3000; // 3 seconds
  private reconnectTimer: NodeJS.Timeout | null = null;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private isIntentionallyClosed = false;

  constructor(baseUrl = process.env.REACT_APP_API_BASE_URL || 'http://127.0.0.1:8000') {
    // Convert http to ws, https to wss
    this.url = baseUrl
      .replace(/^http:/, 'ws:')
      .replace(/^https:/, 'wss:');
  }

  /**
   * Subscribe to WebSocket events
   */
  subscribe(callback: SubscriberCallback): () => void {
    this.subscribers.add(callback);
    
    // Return unsubscribe function
    return () => {
      this.subscribers.delete(callback);
    };
  }

  /**
   * Emit event to all subscribers
   */
  private emit(event: NotificationEvent): void {
    this.subscribers.forEach(callback => {
      try {
        callback(event);
      } catch (error) {
        console.error('Error in subscriber callback:', error);
      }
    });
  }

  /**
   * Connect to WebSocket server
   */
  public async connect(): Promise<void> {
    // Prevent multiple connection attempts
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      console.warn('WebSocket already connected or connecting');
      return;
    }

    // Get token from localStorage
    this.token = localStorage.getItem('token');
    if (!this.token) {
      console.error('No authentication token found');
      this.emit({ type: 'error', error: 'No authentication token' });
      return;
    }

    try {
      this.isIntentionallyClosed = false;
      const wsUrl = `${this.url}/api/v1/notifications/ws?token=${encodeURIComponent(this.token)}`;
      
      console.log('🔌 Connecting to WebSocket:', wsUrl.replace(this.token, '[TOKEN]'));
      
      this.ws = new WebSocket(wsUrl);

      // Setup event handlers
      this.ws.onopen = this.handleOpen.bind(this);
      this.ws.onmessage = this.handleMessage.bind(this);
      this.ws.onerror = this.handleError.bind(this);
      this.ws.onclose = this.handleClose.bind(this);

    } catch (error) {
      console.error('Error creating WebSocket connection:', error);
      this.emit({ type: 'error', error: String(error) });
      this.scheduleReconnect();
    }
  }

  /**
   * Handle successful connection
   */
  private handleOpen(): void {
    console.log('✅ WebSocket connected');
    this.reconnectAttempts = 0;
    this.emit({ type: 'connected' });
    
    // Start heartbeat to keep connection alive
    this.startHeartbeat();
    
    // Request initial unread count
    this.send({
      type: 'get_unread',
      limit: 10
    });
  }

  /**
   * Handle incoming messages
   */
  private handleMessage(event: MessageEvent): void {
    try {
      const message: ServerMessage = JSON.parse(event.data);
      console.log('📨 WebSocket message:', message.type, message);

      switch (message.type) {
        case 'pong':
          console.debug('💓 Heartbeat response received');
          break;

        case 'unread_notifications':
          this.emit({
            type: 'updated',
            data: {
              count: message.count,
              notifications: message.notifications
            }
          });
          break;

        case 'new_notification':
          this.emit({
            type: 'new',
            data: message.notification
          });
          break;

        case 'notification_updated':
          this.emit({
            type: 'updated',
            data: {
              notificationId: message.notification_id,
              success: message.success
            }
          });
          break;

        case 'error':
          console.error('WebSocket server error:', message.message);
          this.emit({
            type: 'error',
            error: message.message || 'Unknown error'
          });
          break;

        default:
          console.warn('Unknown message type:', message.type);
      }
    } catch (error) {
      console.error('Error parsing WebSocket message:', error);
      this.emit({
        type: 'error',
        error: 'Failed to parse message: ' + String(error)
      });
    }
  }

  /**
   * Handle connection errors
   */
  private handleError(event: Event): void {
    console.error('❌ WebSocket error:', event);
    this.emit({
      type: 'error',
      error: 'WebSocket connection error'
    });
  }

  /**
   * Handle connection close
   */
  private handleClose(): void {
    console.log('🔌 WebSocket disconnected');
    this.stopHeartbeat();
    
    if (!this.isIntentionallyClosed) {
      this.emit({ type: 'disconnected' });
      this.scheduleReconnect();
    }
  }

  /**
   * Schedule automatic reconnection
   */
  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached. Giving up.');
      this.emit({
        type: 'error',
        error: 'Failed to reconnect after multiple attempts'
      });
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1); // Exponential backoff
    
    console.log(`📅 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  /**
   * Start heartbeat to keep connection alive
   */
  private startHeartbeat(): void {
    this.stopHeartbeat();
    
    this.heartbeatTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.send({ type: 'ping' });
      }
    }, 30000); // Every 30 seconds
  }

  /**
   * Stop heartbeat
   */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  /**
   * Send message to server
   */
  public send(message: WebSocketMessage): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('WebSocket not connected, cannot send message:', message);
      return;
    }

    try {
      this.ws.send(JSON.stringify(message));
      console.log('📤 WebSocket message sent:', message.type);
    } catch (error) {
      console.error('Error sending WebSocket message:', error);
      this.emit({
        type: 'error',
        error: 'Failed to send message: ' + String(error)
      });
    }
  }

  /**
   * Request unread notifications
   */
  public getUnreadNotifications(limit = 10): void {
    this.send({
      type: 'get_unread',
      limit
    });
  }

  /**
   * Mark notification as read
   */
  public markAsRead(notificationId: string): void {
    this.send({
      type: 'mark_read',
      notification_id: notificationId
    });
  }

  /**
   * Disconnect WebSocket
   */
  public disconnect(): void {
    this.isIntentionallyClosed = true;
    this.stopHeartbeat();
    
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    console.log('🔌 WebSocket disconnected intentionally');
  }

  /**
   * Check if connected
   */
  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /**
   * Get current connection state
   */
  public getState(): string {
    if (!this.ws) return 'CLOSED';
    
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING:
        return 'CONNECTING';
      case WebSocket.OPEN:
        return 'OPEN';
      case WebSocket.CLOSING:
        return 'CLOSING';
      case WebSocket.CLOSED:
        return 'CLOSED';
      default:
        return 'UNKNOWN';
    }
  }
}

// Create and export singleton instance
export const wsService = new WebSocketService();

export default wsService;
