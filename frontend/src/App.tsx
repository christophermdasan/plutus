import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, chat as chatApi, filings as filingsApi } from "./lib/api";
import { useLocalStorage } from "./lib/hooks/useLocalStorage";
import { useResizable } from "./lib/hooks/useResizable";
import { useTheme } from "./lib/hooks/useTheme";
import { ToastProvider, useToast } from "./lib/hooks/useToast";
import { ACTIVE_STATUSES, type Filing, type Message, type SourceRef } from "./lib/types";
import { CommandPalette } from "./components/CommandPalette";
import { ChatView, type Turn } from "./components/chat/ChatView";
import { FilingMenu } from "./components/filings/FilingMenu";
import { Sidebar } from "./components/layout/Sidebar";
import { SourceDrawer } from "./components/source/SourceDrawer";
import { SettingsDialog } from "./components/settings/SettingsDialog";
import { Button, Dialog, Input, Label } from "./components/ui";

const POLL_INTERVAL_MS = 2000;

function AppInner() {
  const toast = useToast();
  const [theme, setTheme] = useTheme();

  const [filings, setFilings] = useState<Filing[]>([]);
  const [view, setView] = useState<"active" | "archive">("active");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [turnsByFiling, setTurnsByFiling] = useState<Record<string, Turn[]>>({});
  const [sessionByFiling, setSessionByFiling] = useState<Record<string, number>>({});
  const [source, setSource] = useState<SourceRef | null>(null);

  const [uploading, setUploading] = useState(false);
  const [collapsed, setCollapsed] = useLocalStorage("sidebar_collapsed", false);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menu, setMenu] = useState<{ filing: Filing; anchor: DOMRect } | null>(null);
  const [renaming, setRenaming] = useState<Filing | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const pollRef = useRef<number | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const pendingFilingsRef = useRef(new Set<string>());

  // Panel widths are a working preference - an analyst comparing a wide table
  // wants the document larger; one reading a long answer wants it smaller.
  const sidebarResize = useResizable({
    storageKey: "plutus_sidebar_width",
    defaultWidth: 268,
    min: 200,
    max: 460,
    edge: "right",
  });
  const drawerResize = useResizable({
    storageKey: "plutus_drawer_width",
    defaultWidth: 560,
    min: 360,
    max: 1100,
    edge: "left",
  });

  function cycleTheme() {
    setTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light");
  }

  /** Back to a clean start screen, ready to add a filing. */
  function goHome() {
    setSelectedId(null);
    setSource(null);
  }

  /** Show or hide the document itself, with no question asked and nothing
   *  quoted. Closing is the same control, so it is a toggle rather than a
   *  one-way open the reader then has to hunt for a way out of. */
  function toggleDocument() {
    if (source) {
      setSource(null);
      return;
    }
    if (!selected) return;
    setSource({
      filingId: selected.id,
      filingName: selected.display_title,
      page: 1,
      quote: "",
    });
  }

  const refresh = useCallback(
    async (archived = view === "archive") => {
      try {
        const list = await filingsApi.list(archived);
        setFilings(list);
        return list;
      } catch {
        return [];
      }
    },
    [view],
  );

  useEffect(() => {
    refresh().then((list) => {
      const firstReady = list.find((f) => f.status === "ready");
      if (firstReady) setSelectedId(firstReady.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  /*
   * Poll the documents that are actually indexing, and only those.
   *
   * This used to re-fetch the whole workspace every two seconds, so watching
   * one upload re-read every other filing with it - work that grows with the
   * size of the library while telling you nothing new about the one document
   * you are waiting on.
   *
   * The id list is joined into a string for the dependency array: the array
   * identity changes on every render, the ids themselves do not, and
   * depending on the array would tear the interval down and rebuild it each
   * time.
   */
  const activeIds = filings.filter((f) => ACTIVE_STATUSES.includes(f.status)).map((f) => f.id);
  const activeKey = activeIds.join(",");

  useEffect(() => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    if (!activeKey) return;

    const ids = activeKey.split(",");
    pollRef.current = window.setInterval(async () => {
      const updated = await Promise.all(
        // A filing deleted mid-ingest 404s; drop it rather than fail the batch.
        ids.map((id) => filingsApi.get(id).catch(() => null)),
      );
      setFilings((previous) =>
        previous.map((filing) => updated.find((u) => u?.id === filing.id) ?? filing),
      );
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [activeKey]);

  /*
   * Say so when an upload finishes, either way.
   *
   * A failure previously showed only as a small "Failed" badge in the
   * sidebar, with the reason recorded on the row but never displayed - so a
   * document that could not be read looked identical to one still being
   * read, and nothing said why.
   */
  const previousStatuses = useRef<Record<string, string>>({});
  useEffect(() => {
    for (const filing of filings) {
      const before = previousStatuses.current[filing.id];
      previousStatuses.current[filing.id] = filing.status;
      if (!before || before === filing.status) continue;
      if (!ACTIVE_STATUSES.includes(before as Filing["status"])) continue;

      if (filing.status === "failed") {
        toast(filing.error || `${filing.display_title} could not be read.`, "error");
      } else if (filing.status === "ready") {
        toast(`${filing.display_title} is ready - ask it anything.`);
      }
    }
  }, [filings, toast]);

  // ⌘K / Ctrl K anywhere.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Restore a filing's history the first time it's opened.
  useEffect(() => {
    if (!selectedId || selectedId in turnsByFiling) return;
    const filingId = selectedId;

    chatApi
      .sessions(filingId)
      .then(async (sessions) => {
        if (sessions.length === 0) {
          setTurnsByFiling((prev) => ({ ...prev, [filingId]: [] }));
          return;
        }
        const latest = sessions[0];
        const messages = await chatApi.messages(latest.id);
        setSessionByFiling((prev) => ({ ...prev, [filingId]: latest.id }));
        setTurnsByFiling((prev) => ({
          ...prev,
          [filingId]: messages.map((m) => ({
            id: `m${m.id}`,
            question: m.question,
            feedback: m.feedback,
            result: {
              message_id: m.id,
              session_id: m.session_id,
              question: m.question,
              found: m.found,
              answer: m.answer,
              page: m.page,
              quote: m.quote,
              // Reloaded history offers the same alternate locations the
              // live answer did. `?? []` guards the rows written before
              // citations were stored, which carry only page and quote.
              citations: m.citations ?? [],
              reason: m.reason,
              considered: [],
              latency_ms: m.latency_ms,
              model: null,
            },
          })),
        }));
      })
      .catch(() => setTurnsByFiling((prev) => ({ ...prev, [filingId]: [] })));
  }, [selectedId, turnsByFiling]);

  const selected = filings.find((f) => f.id === selectedId) ?? null;
  const turns = selectedId ? (turnsByFiling[selectedId] ?? []) : [];

  async function handleUpload(file: File) {
    setUploading(true);
    try {
      const filing = await filingsApi.upload(file);
      if (view === "archive") setView("active");
      await refresh(false);
      setSource(null);
      setSelectedId(filing.id);
      toast(`Added ${filing.original_name}`, "success");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Upload failed", "error");
    } finally {
      setUploading(false);
    }
  }

  async function handleAsk(question: string) {
    if (!selectedId) return;
    const filingId = selectedId;
    if (pendingFilingsRef.current.has(filingId)) return;
    pendingFilingsRef.current.add(filingId);
    const turnId = crypto.randomUUID();
    const filingName = selected?.display_title ?? filingId;

    setTurnsByFiling((prev) => ({
      ...prev,
      [filingId]: [...(prev[filingId] ?? []), { id: turnId, question, pending: true }],
    }));

    const patch = (update: Partial<Turn>) =>
      setTurnsByFiling((prev) => ({
        ...prev,
        [filingId]: (prev[filingId] ?? []).map((t) =>
          t.id === turnId ? { ...t, pending: false, ...update } : t,
        ),
      }));

    try {
      const result = await chatApi.ask(filingId, question, sessionByFiling[filingId]);
      setSessionByFiling((prev) => ({ ...prev, [filingId]: result.session_id }));
      patch({ result });

      // Open the source automatically for a verified answer - seeing the
      // evidence is the point of the product.
      if (result.found && result.page && selectedIdRef.current === filingId) {
        setSource({
          filingId,
          filingName,
          page: result.page,
          // The printed page number, so the panel agrees with the chips.
          // Without it the drawer showed the internal index beside a chip
          // reading two lower, which looks like a bug in the citation.
          label: result.citations?.[0]?.label,
          quote: result.quote,
          question,
          answer: result.answer,
        });
      }
    } catch (err) {
      const rateLimited = err instanceof ApiError && err.isRateLimited;
      patch({
        error: {
          message: err instanceof Error ? err.message : "Something went wrong.",
          hint: err instanceof ApiError ? err.hint : undefined,
          rateLimited,
        },
      });
      if (rateLimited) toast("AI usage limit reached — try again shortly", "error");
    } finally {
      pendingFilingsRef.current.delete(filingId);
    }
  }

  async function handleFeedback(turn: Turn, value: number | null) {
    if (!turn.result || !selectedId) return;
    const filingId = selectedId;
    setTurnsByFiling((prev) => ({
      ...prev,
      [filingId]: (prev[filingId] ?? []).map((t) =>
        t.id === turn.id ? { ...t, feedback: value } : t,
      ),
    }));
    try {
      await chatApi.feedback(turn.result.message_id, value);
    } catch {
      /* feedback is advisory; a failure shouldn't interrupt the user */
    }
  }

  async function handleCopy(turn: Turn) {
    if (!turn.result) return;
    const { answer, page, quote } = turn.result;
    const citation =
      turn.result.citations?.find((candidate) => candidate.page === page) ??
      turn.result.citations?.[0];
    const shownPage = citation?.label ?? page;
    const shownQuote = citation?.quote ?? quote;
    const text = `Q: ${turn.question}\nA: ${answer}\n\nSource: ${selected?.display_title ?? ""}, page ${shownPage}\n"${shownQuote}"`;
    try {
      await navigator.clipboard.writeText(text);
      toast("Answer copied with citation", "success");
    } catch {
      toast("Couldn't copy to clipboard", "error");
    }
  }

  async function openAnswerFromSearch(message: Message) {
    // Search results carry a session, not a filing; look the filing up via
    // its session so the drawer can open on the right document.
    try {
      const sessions = await chatApi.sessions();
      const session = sessions.find((s) => s.id === message.session_id);
      if (!session) throw new Error("The filing for that answer is no longer available.");
      const target =
        filings.find((f) => f.id === session.filing_id) ??
        (await filingsApi.get(session.filing_id));
      const targetView = target.is_archived ? "archive" : "active";
      if (targetView !== view) {
        setView(targetView);
        await refresh(target.is_archived);
      }

      setSelectedId(target.id);
      if (message.found && message.page) {
        setSource({
          filingId: target.id,
          filingName: target.display_title,
          page: message.page,
          label: message.citations?.[0]?.label,
          quote: message.quote,
          question: message.question,
          answer: message.answer,
        });
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't open that answer.", "error");
    }
  }

  async function archiveFiling(filing: Filing) {
    try {
      await filingsApi.archive(filing.id);
      await refresh();
      if (selectedId === filing.id) {
        setSelectedId(null);
        setSource(null);
      }
      toast(`Archived ${filing.display_title}`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't archive that filing.", "error");
    }
  }

  async function unarchiveFiling(filing: Filing) {
    try {
      await filingsApi.unarchive(filing.id);
      await refresh();
      toast(`Restored ${filing.display_title}`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't restore that filing.", "error");
    }
  }

  async function deleteFiling(filing: Filing) {
    try {
      await filingsApi.remove(filing.id);
      await refresh();
      if (selectedId === filing.id) {
        setSelectedId(null);
        setSource(null);
      }
      // Soft delete: say so, so nobody thinks their document is gone.
      toast(`Deleted ${filing.display_title} — the file is kept and can be restored`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't delete that filing.", "error");
    }
  }

  async function submitRename() {
    if (!renaming) return;
    try {
      await filingsApi.rename(renaming.id, renameValue.trim());
      setRenaming(null);
      await refresh();
      toast("Renamed");
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't rename that filing.", "error");
    }
  }

  const sourceFiling = filings.find((filing) => filing.id === source?.filingId) ?? null;

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar
        filings={filings}
        selectedId={selectedId}
        collapsed={collapsed}
        uploading={uploading}
        view={view}
        theme={theme}
        width={sidebarResize.width}
        resizing={sidebarResize.dragging}
        onResizeStart={sidebarResize.onPointerDown}
        onToggleCollapse={() => setCollapsed(!collapsed)}
        onSelect={(id) => {
          setSelectedId(id);
          setSource(null);
        }}
        onUpload={handleUpload}
        onHome={goHome}
        onOpenSettings={() => setSettingsOpen(true)}
        onCycleTheme={cycleTheme}
        onOpenSearch={() => setPaletteOpen(true)}
        onSetView={(nextView) => {
          // Switching between the active library and Archive replaces the
          // filing context, just like returning home. Keep a source drawer
          // from outliving the filing it was opened for.
          setSelectedId(null);
          setSource(null);
          setView(nextView);
        }}
        onFilingMenu={(filing, anchor) => setMenu({ filing, anchor })}
      />

      <ChatView
        filing={selected}
        turns={turns}
        onUpload={handleUpload}
        uploading={uploading}
        sourceOpen={source !== null}
        onToggleDocument={toggleDocument}
        activeSource={source}
        onAsk={handleAsk}
        onOpenSource={setSource}
        onFeedback={handleFeedback}
        onCopy={handleCopy}
      />

      <SourceDrawer
        source={source}
        maxPage={sourceFiling?.num_pages ?? null}
        mediaKind={sourceFiling?.media_kind ?? "pdf"}
        width={drawerResize.width}
        resizing={drawerResize.dragging}
        onResizeStart={drawerResize.onPointerDown}
        onClose={() => setSource(null)}
      />

      {menu && (
        <FilingMenu
          filing={menu.filing}
          anchor={menu.anchor}
          onClose={() => setMenu(null)}
          onRename={(f) => {
            setRenaming(f);
            setRenameValue(f.original_name);
          }}
          onArchive={archiveFiling}
          onUnarchive={unarchiveFiling}
          onDelete={deleteFiling}
        />
      )}

      <Dialog open={renaming !== null} onClose={() => setRenaming(null)} width="max-w-sm">
        <div className="p-5">
          <Label>Rename filing</Label>
          <Input
            value={renameValue}
            autoFocus
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitRename()}
          />
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setRenaming(null)}>Cancel</Button>
            <Button variant="primary" onClick={submitRename} disabled={!renameValue.trim()}>
              Save
            </Button>
          </div>
        </div>
      </Dialog>

      <SettingsDialog
        open={settingsOpen}
        theme={theme}
        onClose={() => setSettingsOpen(false)}
        onThemeChange={setTheme}
      />

      <CommandPalette
        open={paletteOpen}
        filings={filings}
        onClose={() => setPaletteOpen(false)}
        onSelectFiling={(id) => {
          setSelectedId(id);
          setSource(null);
        }}
        onOpenAnswer={openAnswerFromSearch}
      />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
