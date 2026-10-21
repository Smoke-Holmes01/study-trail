import { afterEach, expect, it, vi } from "vitest";
import { subscribe, type Task } from "../src/lib/api";
afterEach(() => vi.unstubAllGlobals());
it("offline closes the old stream and online resumes the same task at its last revision", () => {
  class FakeStream {
    static instances: FakeStream[] = [];
    listeners = new Map<string, (event: MessageEvent) => void>();
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    close = vi.fn();
    constructor(public url: string) {
      FakeStream.instances.push(this);
    }
    addEventListener(name: string, cb: (event: MessageEvent) => void) {
      this.listeners.set(name, cb);
    }
    emit(name: string, data: unknown) {
      this.listeners.get(name)?.({
        data: JSON.stringify({ data }),
      } as MessageEvent);
    }
  }
  vi.stubGlobal("EventSource", FakeStream);
  const update = vi.fn();
  const status = vi.fn();
  const close = subscribe("owned-task", update, status);
  const first = FakeStream.instances[0];
  first.emit("progress", {
    revision: 4,
    status: "running",
    result: { preview_text: "完整快照" },
  } as Task);
  window.dispatchEvent(new Event("offline"));
  expect(first.close).toHaveBeenCalled();
  expect(status).toHaveBeenLastCalledWith(false);
  window.dispatchEvent(new Event("online"));
  expect(FakeStream.instances[1].url).toContain(
    "owned-task/events?after_revision=4",
  );
  first.emit("progress", { revision: 5, status: "running" } as Task);
  expect(update).toHaveBeenCalledTimes(1);
  close();
});
