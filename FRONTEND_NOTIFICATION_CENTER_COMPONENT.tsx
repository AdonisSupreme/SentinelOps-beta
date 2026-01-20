// src/components/notifications/NotificationCenter.tsx
/**
 * NotificationCenter Component
 * 
 * Real-time notification display and management component.
 * Shows connection status, unread count, and notification list.
 */

import React, { useEffect } from 'react';
import useNotifications from '../../hooks/useNotifications';
import './NotificationCenter.css';

export const NotificationCenter: React.FC = () => {
  const {
    unreadCount,
    notifications,
    isConnected,
    error,
    connectionState,
    getUnread,
    markAsRead
  } = useNotifications();

  // Request unread on mount
  useEffect(() => {
    if (isConnected) {
      getUnread(10);
    }
  }, [isConnected, getUnread]);

  return (
    <div className="notification-center">
      {/* Connection Status Indicator */}
      <div className={`connection-status ${isConnected ? 'connected' : 'disconnected'}`}>
        <span className="status-dot"></span>
        <span className="status-text">
          {isConnected ? 'Connected' : `Disconnected (${connectionState})`}
        </span>
      </div>

      {/* Error Display */}
      {error && (
        <div className="notification-error">
          <span>{error}</span>
          <button onClick={() => window.location.reload()}>Retry</button>
        </div>
      )}

      {/* Unread Count Badge */}
      <div className="unread-badge">
        <span className="count">{unreadCount}</span>
        <span className="label">Unread</span>
      </div>

      {/* Notifications List */}
      <div className="notifications-list">
        {notifications.length === 0 ? (
          <div className="empty-state">
            <p>No notifications</p>
          </div>
        ) : (
          notifications.map((notification, index) => (
            <div
              key={notification.id || index}
              className="notification-item"
            >
              <div className="notification-header">
                <h4>{notification.title || 'Notification'}</h4>
                <span className="notification-time">
                  {new Date(notification.created_at).toLocaleString()}
                </span>
              </div>
              <p className="notification-message">
                {notification.message || notification.content}
              </p>
              {!notification.is_read && (
                <button
                  className="mark-read-btn"
                  onClick={() => markAsRead(notification.id)}
                >
                  Mark as read
                </button>
              )}
            </div>
          ))
        )}
      </div>

      {/* Debug Info */}
      <details className="debug-info">
        <summary>Debug Info</summary>
        <pre>
          {JSON.stringify(
            {
              isConnected,
              connectionState,
              unreadCount,
              notificationCount: notifications.length,
              error
            },
            null,
            2
          )}
        </pre>
      </details>
    </div>
  );
};

export default NotificationCenter;
