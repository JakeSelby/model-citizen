import { Badge, Button, Group, Text } from "@mantine/core";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  OrderedEventGate,
  PendingUpdates,
  parseLiveUpdate,
  updateTouchesTopics,
  type LiveUpdate,
} from "./model";

type ConnectionState = "connecting" | "current" | "stale";
type Subscriber = (event: LiveUpdate) => void;

interface LiveContextValue {
  connection: ConnectionState;
  paused: boolean;
  queued: number;
  pause: () => void;
  resume: () => void;
  subscribe: (subscriber: Subscriber) => () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveUpdatesProvider({ children }: { children: ReactNode }) {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [paused, setPaused] = useState(false);
  const [queued, setQueued] = useState(0);
  const pausedRef = useRef(false);
  const queueRef = useRef(new PendingUpdates());
  const subscribers = useRef(new Set<Subscriber>());

  const dispatch = useCallback((event: LiveUpdate) => {
    for (const subscriber of subscribers.current) subscriber(event);
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/live");
    const gate = new OrderedEventGate();
    const receive = (raw: Event) => {
      const event = parseLiveUpdate((raw as MessageEvent<string>).data);
      if (!event || !gate.accepts(event)) return;
      if (pausedRef.current) {
        queueRef.current.push(event);
        setQueued(queueRef.current.size);
      } else {
        dispatch(event);
      }
    };
    source.addEventListener("change", receive);
    source.addEventListener("gap", receive);
    source.addEventListener("snapshot", receive);
    source.onopen = () => setConnection("current");
    source.onerror = () => setConnection("stale");
    return () => source.close();
  }, [dispatch]);

  const pause = useCallback(() => {
    pausedRef.current = true;
    setPaused(true);
  }, []);
  const resume = useCallback(() => {
    const pending = queueRef.current.drain();
    pausedRef.current = false;
    setPaused(false);
    setQueued(0);
    if (pending) dispatch(pending);
  }, [dispatch]);
  const subscribe = useCallback((subscriber: Subscriber) => {
    subscribers.current.add(subscriber);
    return () => { subscribers.current.delete(subscriber); };
  }, []);
  const value = useMemo(() => ({ connection, paused, queued, pause, resume, subscribe }),
    [connection, pause, paused, queued, resume, subscribe]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

function useLiveContext(): LiveContextValue {
  const context = useContext(LiveContext);
  if (!context) throw new Error("live updates require LiveUpdatesProvider");
  return context;
}

export function useLiveUpdates(topics: string[], callback: (event: LiveUpdate) => void,
  predicate?: (event: LiveUpdate) => boolean) {
  const { subscribe } = useLiveContext();
  const callbackRef = useRef(callback);
  const predicateRef = useRef(predicate);
  callbackRef.current = callback;
  predicateRef.current = predicate;
  const topicKey = [...topics].sort().join("\n");

  useEffect(() => {
    const selected = new Set(topicKey ? topicKey.split("\n") : []);
    return subscribe((event) => {
      if (updateTouchesTopics(event, selected) && (!predicateRef.current || predicateRef.current(event))) {
        callbackRef.current(event);
      }
    });
  }, [subscribe, topicKey]);
}

export function LiveUpdateControls() {
  const { connection, pause, paused, queued, resume } = useLiveContext();
  const label = connection === "current" ? "Live" : connection === "connecting" ? "Connecting" : "Stale";
  const color = connection === "current" ? "teal" : connection === "connecting" ? "blue" : "orange";
  return <Group gap="xs" wrap="nowrap">
    <Badge color={color} variant="light">{label}</Badge>
    {paused && queued > 0 ? <Text aria-live="polite" size="xs">{queued} queued</Text> : null}
    <Button onClick={paused ? resume : pause} size="compact-xs" variant="subtle">
      {paused ? "Resume" : "Pause"}
    </Button>
  </Group>;
}
