/**
 * TypeScript mirrors of the backend's JSON payloads.
 *
 * Field names match the FastAPI models exactly — they are the wire format, so
 * a rename on either side has to be mirrored here.
 */

import type { ObservationPayload } from "@/lib/api/client";

export type ProjectInfo = {
  name: string;
  path: string;
  active: boolean;
  /** Opened where it stands rather than made here, so removing it keeps the files. */
  linked?: boolean;
};

export type TreeFile = {
  path: string;
  name: string;
  title: string;
};

type SourceCitationNote = {
  path: string;
  title: string;
};

type SourceCitationMark = {
  id: string;
  path: string;
  title: string;
  heading: string;
  collectionId: string;
  blockIds: string[];
};

export type CompiledClipCollection = {
  id: string;
  sourceKey: string;
  sourceLabel: string;
  blockIds: string[];
};

export type SourceCitationBundle = {
  notes: SourceCitationNote[];
  marks: SourceCitationMark[];
};

export type ResourceCard = {
  title: string;
  url: string;
  site: string;
  siteName: string;
  favicon: string;
  snippet: string;
  via: string;
  why: string;
  intake?: string;
};

export type DerivedBlock = {
  id: string;
  tag: string;
  text: string;
  preview: string;
};

export type CapturePreview = {
  url: string;
  finalUrl: string;
  title: string;
  markdown: string;
  html?: string;
  blocks?: DerivedBlock[];
  captureMethod: string;
  fidelity: "ok" | "low" | "failed" | string;
  reasons: string[];
  httpStatus: number | null;
  charCount: number;
  imageCount?: number;
  imagesFailed?: number;
  kind?: string;
  pdfBase64?: string;
  officeBase64?: string;
  imageBase64?: string;
  imageMediaType?: string;
  pageCount?: number;
  intake?: string;
};

export type RawSaveItem = {
  code: string;
  path: string;
  url: string;
  message: string;
};

/* ------------------------------------------------------------ Relationships */

export type WikiGraphNode = {
  id: string;
  path: string | null;
  title: string;
  type: string;
  ghost: boolean;
  degree: number;
};

export type WikiGraphEdge = {
  source: string;
  target: string;
  count: number;
};

/* -------------------------------------------------------------- Deep Search */

type DeepSearchSeed = {
  kind: string;
  path: string;
  slug: string;
  title: string;
};

/**
 * A page a round proposes, or a plan works out. The same shape as a plan's page,
 * because it is the same thing at an earlier stage — the list the round proposed,
 * and the plan that refined it, are one list.
 *
 * `plan` is the model's sentence about it and arrives in the language the interface
 * asked for. `reason` is a code for a reason the core itself worked out — the core
 * cannot know what language the reader is using, so it sends the code and its
 * figures and the interface says them.
 */
export type DeepSearchPage = TopicNode & {
  reason?: string;
  reasonArgs?: Record<string, string>;
};

type DeepSearchEdge = {
  source: string;
  target: string;
  relation: string;
};

export type DeepSearchBlock = {
  pageSlug: string;
  locator: string;
  preview: string;
  accepted: boolean;
};

export type DeepSearchCard = {
  pageSlug: string;
  title: string;
  url: string;
  snippet: string;
  site: string;
  picked: boolean;
};

type DeepSearchPotentialFile = {
  path: string;
  leftover: number;
  total: number;
};

type DeepSearchPotential = {
  sourceFiles: number;
  filesWithRemainder: number;
  remainderBlocks: number;
  topFiles: DeepSearchPotentialFile[];
  chewDisabled: boolean;
  lowPotential: boolean;
};

export type DeepSearchSession = {
  version: number;
  roundId: string;
  mode: string;
  step: number;
  intent: string;
  seed: DeepSearchSeed | null;
  whitelist: DeepSearchPage[];
  whitelistOmitted: number;
  overlayEdges: DeepSearchEdge[];
  proposedBlocks: DeepSearchBlock[];
  cards: DeepSearchCard[];
  cardsOmitted: number;
  collectionIds: string[];
  ingestedIds: string[];
  truncated: boolean;
  notice: string;
  potential?: DeepSearchPotential;
};

/* ---------------------------------------------------------- Plan and compile */

type TopicSection = {
  id: string;
  title: string;
  blockIds: string[];
  collectionId?: string;
  role?: string;
};

type CollectionSummary = {
  id: string;
  title: string;
  gist: string;
};

type TopicCoverage = "write" | "mention" | "skip";

type CollectionRole = {
  collectionId: string;
  role: string;
};

/**
 * One page a pass has proposed, from first mention to planned content.
 *
 * A round proposes pages before it has read anything and its plan refines them once
 * material is in hand; those are two stages of the same proposal, so both arrive in
 * this shape. Which fields are empty says how far along the pass is.
 */
export type TopicNode = {
  /** What the page is called. One name for it, everywhere. */
  title: string;
  slug: string;
  /** Is the page already there? Empty when nobody has said. */
  action?: "" | "add" | "revise";
  /** How much of it to write. Only a plan decides this. */
  coverage?: TopicCoverage;
  /** Whether the reader still wants it. A search's own list carries this. */
  accepted?: boolean;
  /** The question to search for, while a search goes looking. */
  query?: string;
  /** A named part of the page the material is expected to land in. */
  section?: string;
  /** Why the round wants it, written before anything was read. */
  plan?: string;
  /** Where it will live, once the plan has said. */
  wikiPath?: string;
  relationToPrimary?: string;
  collectionIds?: string[];
  collectionRoles?: CollectionRole[];
  blockIds?: string[];
  /** Why the plan wants it, pointing at the passages it read. */
  evidence?: string;
  outline?: TopicSection[];
  /**
   * True when the pages this pass was given do not include this one — material that
   * turned up while reading. Kept and shown rather than dropped, because the page to
   * write is a decision for whoever is looking at it.
   */
  unlisted?: boolean;
};

export type TopicEdge = {
  from: string;
  to: string;
  relation: string;
  evidence?: string;
  collectionId?: string;
  blockIds?: string[];
};

export type TopicMap = {
  intent: string;
  collectionSummaries?: CollectionSummary[];
  candidates?: TopicNode[];
  primary: TopicNode;
  alsoInSources: TopicNode[];
  edges?: TopicEdge[];
  notes: string;
};

export type TopicAction = "write" | "skip" | "mention";

export type ConfirmedPage = {
  name: string;
  slug: string;
  wikiPath: string;
  action: TopicAction;
  sectionIds: string[];
};

export type DoctorFinding = {
  severity: string;
  code: string;
  message: string;
  path: string | null;
  /** The figures `code` means, for wording the finding in the reader's language. */
  params?: Record<string, string>;
};

export type DoctorPayload = {
  contentPages: number;
  indexLinks: number;
  errors: number;
  warnings: number;
  findingCount: number;
  truncated: boolean;
  findings: DoctorFinding[];
};

type IngestWritten = {
  path: string;
  mode: string;
  bytes: number;
};

type IngestSourceResult = {
  sourceIdentity: string;
  sourceSummaryPath: string;
  cacheHit: boolean;
  analysisChars: number;
  generationChars: number;
  parsedBlocks: number;
  dryRun: boolean;
  written: IngestWritten[];
  writeWarnings: { code: string; message: string; path: string }[];
  parseWarnings: string[];
  indexAdded: number;
  indexSkipped: number;
  doctor: DoctorPayload | null;
};

export type IngestDone = {
  results: IngestSourceResult[];
  errors: { path: string; detail: string }[];
};

/* -------------------------------------------------------- Selected passages */

type ClipCollectionPayload = {
  id: string;
  sourceKey: string;
  sourceLabel: string;
  blockIds: string[];
  blocks: DerivedBlock[];
  roundId?: string;
  targetSlug?: string;
};

type GateDecisionPayload = {
  slug: string;
  action: TopicAction;
  sectionIds: string[];
};

export type ClipsPayload = {
  intent: string;
  collections: ClipCollectionPayload[];
  topicMap: TopicMap | null;
  /** The gate: the answers belonging to the pass that was confirmed. */
  decisions?: GateDecisionPayload[];
  /** The board: what the reader answered, which a later plan carries over. */
  choices?: GateDecisionPayload[];
};

/* --------------------------------------------------------- Source revisions */

type RevisionInboxKind =
  | "raw_modified"
  | "raw_created"
  | "raw_deleted"
  | "clips_changed"
  | "locator_broken"
  | "output_missing";

export type RevisionInboxItem = {
  identity: string;
  kind: RevisionInboxKind | string;
  token: string;
  status: "open" | "ignored" | "acknowledged";
  filesWritten: string[];
  locators: string[];
  updatedAt: string;
};

export type RevisionInboxPayload = {
  project: string;
  openCount: number;
  items: RevisionInboxItem[];
};

export type IngestRecoveryJob = {
  id: string;
  intent: string;
  confirmed: ConfirmedPage[];
  writePaths: string[];
  status: string;
  error: string;
  updatedAt: string;
};

/**
 * The last gated compile that finished, as the project keeps it.
 *
 * A round that is over, not a job waiting for anyone: what it wrote, what the
 * check made of the wiki afterwards, and what the write refused.
 */
export type CompletedRoundPayload = {
  intent: string;
  finishedAt: string;
  written: { path: string; mode: string }[];
  doctor: DoctorPayload | null;
  writeWarnings: { code: string; message: string; path: string }[];
  parseWarnings: string[];
};

export type IngestRecoveryPayload = {
  project: string;
  openCount: number;
  items: IngestRecoveryJob[];
  /** `null` when no gated compile has finished yet. */
  last: CompletedRoundPayload | null;
};

/* ------------------------------------------------------------------- Chat */

export type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  /** What the answer was built from; absent on a question and on older turns. */
  cards?: ObservationPayload[];
};

export type ChatLimits = {
  historyLimit: number;
  maxMessages: number;
  maxContentChars: number;
  maxConversations: number;
  maxTitleChars: number;
  maxBriefChars: number;
  maxIndexChars: number;
  maxOpenPages: number;
  maxPageChars: number;
  maxPagesChars: number;
  maxToolRounds: number;
};

/** How far each knob may be turned, as the core reports it. */
export type ChatLimitBounds = Record<keyof ChatLimits, { min: number; max: number }>;

/**
 * The limits and the ranges they may take.
 *
 * The ranges travel with the values so that a view showing the knobs can say what is
 * wrong with one without keeping its own copy of the numbers, which would be a copy
 * that can drift away from what the core enforces.
 */
export type ChatLimitsPayload = ChatLimits & { bounds?: ChatLimitBounds };

export type ChatSummary = {
  id: string;
  title: string;
  updatedAt: string;
  messageCount: number;
};

export type ChatConversation = {
  id: string;
  title: string;
  updatedAt: string;
  messages: ChatTurn[];
};

export type ChatListPayload = {
  limits: ChatLimitsPayload;
  conversations: ChatSummary[];
};
