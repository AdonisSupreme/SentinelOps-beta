// src/hooks/useNotifications.ts
/**
 * React Hook for WebSocket Notifications
 * 
 * Usage:
 * const { unreadCount, notifications, isConnected, error } = useNotifications();
 */

import { useState, useEffect, useCallback } from 'react';
import { wsService, NotificationEvent } from '../services/websocket';
import { useAuth } from '../contexts/AuthContext';

export interface UseNotificationsReturn {
  unreadCount: number;
  notifications: any[];
  isConnected: boolean;
  error: string | null;
  getUnread: (limit?: number) => void;
  markAsRead: (notificationId: string) => void;
  connectionState: string;
}

export const useNotifications = (): UseNotificationsReturn => {
  const { isAuthenticated, token } = useAuth();
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState('CLOSED');

  // Handle WebSocket events
  const handleNotificationEvent = useCallback((event: NotificationEvent) => {
    console.log('Notification event:', event.type, event);

    switch (event.type) {
      case 'connected':
        setIsConnected(true);
        setError(null);
        setConnectionState('OPEN');
        break;

      case 'disconnected':
        setIsConnected(false);
        setConnectionState('CLOSED');
        break;

      case 'new':
        // New notification received
        if (event.data) {
          setNotifications(prev => [event.data, ...prev].slice(0, 50)); // Keep last 50
          setUnreadCount(prev => prev + 1);
          
          // You could add desktop notification here
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('New Notification', {
              body: event.data.message || 'You have a new notification',
              icon: '/notification-icon.png'
            });
          }
        }
        break;

      case 'updated':
        if (event.data?.count !== undefined) {
          setUnreadCount(event.data.count);
        }
        if (event.data?.notifications) {
          setNotifications(event.data.notifications);
        }
        break;

      case 'error':
        setError(event.error || 'Unknown error');
        console.error('WebSocket error:', event.error);
        break;
    }
  }, []);

  // Connect/Disconnect on mount/unmount
  useEffect(() => {
    if (!isAuthenticated || !token) {
      wsService.disconnect();
      setIsConnected(false);
      return;
    }

    // Subscribe to events
    const unsubscribe = wsService.subscribe(handleNotificationEvent);

    // Connect if not already connected
    if (!wsService.isConnected()) {
      wsService.connect();
    }

    // Update connection state
    setConnectionState(wsService.getState());
    setIsConnected(wsService.isConnected());

    // Cleanup on unmount
    return () => {
      unsubscribe();
    };
  }, [isAuthenticated, token, handleNotificationEvent]);

  // Public methods
  const getUnread = useCallback((limit = 10) => {
    wsService.getUnreadNotifications(limit);
  }, []);

  const markAsRead = useCallback((notificationId: string) => {
    wsService.markAsRead(notificationId);
  }, []);

  return {
    unreadCount,
    notifications,
    isConnected,
    error,
    getUnread,
    markAsRead,
    connectionState
  };
};

export default useNotifications;
