import { afterEach, describe, expect, it, vi } from "vitest";
import { mergePatch } from "./useClassroom";
import {
  ClassroomRequestError,
  createClassroomCommands,
  roomRequest,
  type CommandFeedback,
} from "./classroomCommands";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("classroom command recovery", () => {
  const context = {
    code: "ABCD2345",
    runId: "run",
    token: "participant-token",
    assignmentId: "assignment-1",
  };

  it("bounds waiting for the response body, even if fetch ignores abort", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => new Promise(() => undefined),
    }));
    const request = roomRequest("/rooms", { name: "teacher" }, undefined, 50);
    const checked = expect(request).rejects.toMatchObject({ uncertain: true });
    await vi.advanceTimersByTimeAsync(50);
    await checked;
  });

  it("retries only on request with the same ID and original payload, then returns the result to its caller", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new TypeError("network lost after sending"))
      .mockResolvedValueOnce(Response.json({ signed: { id: "signed-tx" } }));
    vi.stubGlobal("fetch", fetch);
    let status: CommandFeedback | undefined;
    const commands = createClassroomCommands<{ signed: { id: string } }>((next) => { status = next; });
    const payload = { draftId: "draft-1" };
    const original = commands.execute(context, "sign", payload);
    payload.draftId = "different-draft";
    await vi.waitFor(() => expect(status?.pending?.action).toBe("sign"));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(await commands.execute(context, "submit", {})).toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(1);
    commands.retry();
    await expect(original).resolves.toEqual({ signed: { id: "signed-tx" } });
    const first = JSON.parse(fetch.mock.calls[0][1].body);
    const second = JSON.parse(fetch.mock.calls[1][1].body);
    expect(second).toEqual(first);
    expect(first).toMatchObject({ assignmentId: "assignment-1", payload: { draftId: "draft-1" } });
    expect(status).toEqual({ busy: false, pending: null, error: "" });
  });

  it("does not offer uncertain retry after a server-confirmed rollback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      error: { code: "SAVE_FAILED", message: "저장 실패로 되돌렸습니다" },
    }, { status: 503 })));
    let status: CommandFeedback | undefined;
    const commands = createClassroomCommands((next) => { status = next; });
    await expect(commands.execute(context, "submit")).resolves.toBeUndefined();
    expect(status?.pending).toBeNull();
    expect(status?.error).toContain("되돌렸습니다");
  });

  it("discards late results when authority changes, without retrying the old command", async () => {
    let finish: ((response: Response) => void) | undefined;
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(Response.json({ draft: "new-draft" }));
    vi.stubGlobal("fetch", fetch);
    const commands = createClassroomCommands<{ draft: string }>(() => undefined);
    const old = commands.execute(context, "draft", { amount: 30 });
    commands.cancel("역할 변경");
    await expect(old).resolves.toBeUndefined();
    const next = commands.execute({ ...context, assignmentId: "assignment-2" }, "draft", { amount: 10 });
    finish!(Response.json({ draft: "old-draft" }));
    await expect(next).resolves.toEqual({ draft: "new-draft" });
    commands.retry();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetch.mock.calls[1][1].body).assignmentId).toBe("assignment-2");
  });

  it("distinguishes a known error from an ambiguous broken successful response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("invalid json", { status: 200 })));
    await expect(roomRequest("/commands", {})).rejects.toBeInstanceOf(ClassroomRequestError);
    await expect(roomRequest("/commands", {})).rejects.toMatchObject({ uncertain: true });
  });

  it("accepts the stream's leave confirmation but does not treat ended as a successful transfer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => undefined)));
    const commands = createClassroomCommands<{ ok: boolean }>(() => undefined);
    const leave = commands.execute(context, "leave");
    commands.confirmEnded({ ok: true });
    await expect(leave).resolves.toEqual({ ok: true });
    const submit = commands.execute(context, "submit");
    commands.confirmEnded({ ok: true });
    await expect(submit).resolves.toBeUndefined();
  });
});

describe("classroom deltas", () => {
  it("removes consumed UTXOs without mutating the previous snapshot", () => {
    const before = {
      seq: 1,
      lab: {
        nodes: {
          A: {
            utxos: { spent: { amount: 100 }, keep: { amount: 2 } },
            mempool: [{ id: "tx" }],
          },
        },
      },
    };
    const next = mergePatch(before, {
      seq: 2,
      lab: {
        nodes: {
          A: {
            utxos: {
              $replace: { keep: { amount: 2 }, change: { amount: 69 } },
            },
            mempool: [],
          },
        },
      },
    });
    expect(next).toEqual({
      seq: 2,
      lab: {
        nodes: {
          A: {
            utxos: { keep: { amount: 2 }, change: { amount: 69 } },
            mempool: [],
          },
        },
      },
    });
    expect(before.lab.nodes.A.utxos.spent.amount).toBe(100);
  });
  it("clears nullable race state and retains unchanged siblings", () => {
    expect(
      mergePatch(
        { lab: { race: { id: "old" }, difficulty: 1 } },
        { lab: { race: null } },
      ),
    ).toEqual({ lab: { race: null, difficulty: 1 } });
  });
});
