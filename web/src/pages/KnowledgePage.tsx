import { useState, useEffect, useRef } from 'react';
import {
  Plus,
  FileText,
  Link as LinkIcon,
  Trash2,
  Download,
  Search,
  ArrowLeft,
  ExternalLink,
} from 'lucide-react';
import { api } from '../api';
import type { KnowledgeSource, KnowledgeDetail, KnowledgeHit } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Badge } from '../components/Badge';
import { Input, Textarea } from '../components/Input';
import { Loading } from '../components/Loading';
import { EmptyState } from '../components/EmptyState';
import { KnowledgeSearch } from '../components/KnowledgeSearch';
import { formatDateTime } from '../utils';
import { formatApiError } from '../utils/errors';
import { safeExternalUrl } from '../utils/url';
import './KnowledgePage.css';

const PAGE_SIZE = 20;

const KIND_LABELS: Record<KnowledgeSource['kind'], string> = {
  text: '文本',
  pdf: 'PDF',
  url: '链接',
  bookmark: '收藏',
};

const STATUS_LABELS: Record<KnowledgeSource['status'], string> = {
  pending: '等待处理',
  processing: '处理中',
  ready: '可检索',
  failed: '处理失败',
};

const STATUS_VARIANTS: Record<
  KnowledgeSource['status'],
  'default' | 'success' | 'warning' | 'danger' | 'info'
> = {
  pending: 'info',
  processing: 'default',
  ready: 'success',
  failed: 'danger',
};

function StatusBadge({ status }: { status: KnowledgeSource['status'] }) {
  return <Badge variant={STATUS_VARIANTS[status]}>{STATUS_LABELS[status]}</Badge>;
}

function locationText(page?: number | null, paragraph?: number | null): string {
  const parts: string[] = [];
  if (page != null) parts.push(`第 ${page} 页`);
  if (paragraph != null) parts.push(`第 ${paragraph} 段`);
  return parts.join(' · ');
}

export function KnowledgePage() {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [error, setError] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [visibleChunks, setVisibleChunks] = useState(PAGE_SIZE);
  const [highlight, setHighlight] = useState<KnowledgeHit | null>(null);
  const pollTimerRef = useRef<number>();
  const detailTimerRef = useRef<number>();
  const mountedRef = useRef(true);
  const listSeq = useRef(0);
  const detailSeq = useRef(0);
  const sourcesRef = useRef<KnowledgeSource[]>([]);

  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  useEffect(() => {
    mountedRef.current = true;
    loadSources();
    return () => {
      mountedRef.current = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      if (detailTimerRef.current) clearTimeout(detailTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!detailId) return;
    loadDetail(detailId);
  }, [detailId]);

  // 搜索命中的片段可能不在第一批中，展开到能包含它的位置后再滚动定位。
  useEffect(() => {
    if (!detail || !highlight) return;
    const index = detail.chunks.findIndex((chunk) => chunk.id === highlight.id);
    if (index >= visibleChunks) {
      setVisibleChunks(Math.ceil((index + 1) / PAGE_SIZE) * PAGE_SIZE);
      return;
    }
    document.getElementById(`chunk-${highlight.id}`)?.scrollIntoView({ block: 'center' });
  }, [highlight, detail, visibleChunks]);

  async function loadSources(isBackground = false) {
    const seq = ++listSeq.current;
    const limit = Math.max(PAGE_SIZE, sourcesRef.current.length);
    try {
      if (!isBackground) setInitialLoading(sourcesRef.current.length === 0);
      const data = await api.listKnowledge(limit, 0);
      if (!mountedRef.current || seq !== listSeq.current) return;
      setSources(data);
      setHasMore(data.length === limit && limit < 100);
      setError('');
      if (data.some((source) => ['pending', 'processing'].includes(source.status))) {
        schedulePoll();
      }
    } catch (err) {
      if (mountedRef.current && seq === listSeq.current) setError(formatApiError(err));
    } finally {
      if (mountedRef.current && seq === listSeq.current) setInitialLoading(false);
    }
  }

  async function loadMore() {
    setLoadingMore(true);
    const seq = listSeq.current;
    const offset = sourcesRef.current.length;
    try {
      const data = await api.listKnowledge(PAGE_SIZE, offset);
      if (!mountedRef.current || seq !== listSeq.current) return;
      setSources((items) => [
        ...items,
        ...data.filter((item) => !items.some((old) => old.id === item.id)),
      ]);
      setHasMore(data.length === PAGE_SIZE);
    } catch (err) {
      if (mountedRef.current) setError(formatApiError(err));
    } finally {
      if (mountedRef.current) setLoadingMore(false);
    }
  }

  function schedulePoll() {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    pollTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current) loadSources(true);
    }, 2000);
  }

  async function loadDetail(id: string) {
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    setDetailError('');
    try {
      const data = await api.getKnowledge(id);
      if (!mountedRef.current || seq !== detailSeq.current) return;
      setDetail(data);
      if (['pending', 'processing'].includes(data.status)) {
        if (detailTimerRef.current) clearTimeout(detailTimerRef.current);
        detailTimerRef.current = window.setTimeout(() => {
          if (mountedRef.current && detailSeq.current === seq) loadDetail(id);
        }, 2000);
      }
    } catch (err) {
      if (mountedRef.current && seq === detailSeq.current) setDetailError(formatApiError(err));
    } finally {
      if (mountedRef.current && seq === detailSeq.current) setDetailLoading(false);
    }
  }

  function openDetail(id: string, hit?: KnowledgeHit) {
    setHighlight(hit ?? null);
    setVisibleChunks(PAGE_SIZE);
    if (id === detailId) {
      loadDetail(id);
      return;
    }
    setDetail(null);
    setDetailId(id);
  }

  function closeDetail() {
    const previous = detailId;
    detailSeq.current += 1;
    if (detailTimerRef.current) clearTimeout(detailTimerRef.current);
    setDetailId(null);
    setDetail(null);
    setHighlight(null);
    setDetailError('');
    // 关掉详情后把焦点送回触发它的资料标题，键盘操作不会丢位。
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>(`[data-source-id="${previous}"]`)?.focus();
    });
  }

  async function handleDelete(id: string) {
    if (!confirm('确定删除该资料？')) return;
    try {
      await api.deleteKnowledge(id);
      if (id === detailId) closeDetail();
      await loadSources();
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  if (initialLoading) return <Loading text="加载中" />;

  return (
    <div className="knowledge-page">
      <div className="knowledge-header">
        <h1 className="knowledge-title">参考资料</h1>
        <div className="knowledge-header-actions">
          <Button
            variant="secondary"
            onClick={() => setShowSearch(!showSearch)}
            aria-expanded={showSearch}
          >
            <Search size={16} />
            搜索
          </Button>
          <Button onClick={() => setShowForm(!showForm)} aria-expanded={showForm}>
            <Plus size={16} />
            添加资料
          </Button>
        </div>
      </div>

      {error && (
        <div className="knowledge-error" role="alert">
          {error}
        </div>
      )}

      {showSearch && <KnowledgeSearch onOpen={openDetail} />}

      {showForm && (
        <KnowledgeForm
          onSuccess={() => {
            setShowForm(false);
            loadSources(true);
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {detailId ? (
        <KnowledgeDetailCard
          detail={detail}
          loading={detailLoading}
          error={detailError}
          highlight={highlight}
          visibleChunks={visibleChunks}
          onShowMore={() => setVisibleChunks((count) => count + PAGE_SIZE)}
          onClose={closeDetail}
          onDelete={() => handleDelete(detailId)}
          onRetry={() => loadDetail(detailId)}
        />
      ) : sources.length === 0 ? (
        <EmptyState
          icon={<FileText size={24} />}
          title="暂无资料"
          action={
            <Button onClick={() => setShowForm(true)}>
              <Plus size={16} />
              添加资料
            </Button>
          }
        />
      ) : (
        <>
          <div className="knowledge-list">
            {sources.map((source) => (
              <Card key={source.id}>
                <div className="knowledge-item">
                  <div className="knowledge-item-icon" aria-hidden="true">
                    {source.kind === 'url' || source.kind === 'bookmark' ? (
                      <LinkIcon size={20} />
                    ) : (
                      <FileText size={20} />
                    )}
                  </div>
                  <div className="knowledge-item-main">
                    <button
                      type="button"
                      className="knowledge-item-title"
                      data-source-id={source.id}
                      onClick={() => openDetail(source.id)}
                    >
                      {source.title}
                    </button>
                    {source.url && <div className="knowledge-item-url">{source.url}</div>}
                    <div className="knowledge-item-meta">
                      <StatusBadge status={source.status} />
                      <span className="knowledge-item-kind">{KIND_LABELS[source.kind]}</span>
                      <span className="knowledge-item-time">
                        {formatDateTime(source.created_at)}
                      </span>
                    </div>
                    {source.error && (
                      <div className="knowledge-item-error">{source.error.message}</div>
                    )}
                  </div>
                  <div className="knowledge-item-actions">
                    <a
                      href={api.getOriginalUrl(source.id)}
                      download
                      className="knowledge-download-link"
                      aria-label={`下载原文：${source.title}`}
                    >
                      <Download size={14} />
                    </a>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => handleDelete(source.id)}
                      aria-label={`删除资料：${source.title}`}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
          {hasMore && (
            <div className="knowledge-more">
              <Button variant="secondary" onClick={loadMore} loading={loadingMore}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

interface DetailProps {
  detail: KnowledgeDetail | null;
  loading: boolean;
  error: string;
  highlight: KnowledgeHit | null;
  visibleChunks: number;
  onShowMore: () => void;
  onClose: () => void;
  onDelete: () => void;
  onRetry: () => void;
}

function KnowledgeDetailCard({
  detail,
  loading,
  error,
  highlight,
  visibleChunks,
  onShowMore,
  onClose,
  onDelete,
  onRetry,
}: DetailProps) {
  const chunks = detail?.chunks ?? [];
  const shown = chunks.slice(0, visibleChunks);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const focusedKey = useRef<string | null>(null);

  // 打开详情后把焦点放到标题；从搜索结果进来时放到命中片段。
  // 轮询更新同一份详情时不重复抢焦点。
  useEffect(() => {
    if (!detail) return;
    const key = `${detail.id}:${highlight?.id ?? ''}`;
    if (focusedKey.current === key) return;
    if (highlight) {
      const chunk = document.getElementById(`chunk-${highlight.id}`);
      if (!chunk) return;
      chunk.focus();
    } else {
      titleRef.current?.focus();
    }
    focusedKey.current = key;
  }, [detail, highlight, visibleChunks]);

  const url = safeExternalUrl(detail?.url);

  return (
    <Card>
      <div className="knowledge-detail">
        <div className="knowledge-detail-header">
          <Button variant="secondary" size="sm" onClick={onClose}>
            <ArrowLeft size={16} />
            返回列表
          </Button>
          <div className="knowledge-detail-actions">
            {detail && (
              <a
                href={api.getOriginalUrl(detail.id)}
                download
                className="knowledge-download-link"
                aria-label={`下载原文：${detail.title}`}
              >
                <Download size={14} />
              </a>
            )}
            <Button size="sm" variant="secondary" onClick={onDelete} aria-label="删除资料">
              <Trash2 size={14} />
            </Button>
          </div>
        </div>

        {error && (
          <div className="knowledge-error" role="alert">
            {error}
            {!detail && (
              <Button size="sm" variant="secondary" onClick={onRetry} className="detail-retry">
                重试
              </Button>
            )}
          </div>
        )}

        {!detail && loading && <Loading text="加载中" />}

        {detail && (
          <>
            <div className="knowledge-detail-title-row">
              <h2 className="knowledge-detail-title" tabIndex={-1} ref={titleRef}>
                {detail.title}
              </h2>
              <StatusBadge status={detail.status} />
            </div>
            {detail.url &&
              (url ? (
                <a
                  className="knowledge-detail-url"
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ExternalLink size={12} />
                  {detail.url}
                </a>
              ) : (
                <span className="knowledge-detail-url">{detail.url}</span>
              ))}
            <div className="knowledge-detail-meta">
              <span>{KIND_LABELS[detail.kind]}</span>
              <span>{formatDateTime(detail.created_at)}</span>
              {detail.status === 'ready' && <span>{chunks.length} 段</span>}
            </div>
            {detail.error && <div className="knowledge-item-error">{detail.error.message}</div>}

            {detail.status === 'ready' && chunks.length === 0 && (
              <div className="knowledge-detail-empty">没有可显示的片段</div>
            )}

            <ul className="chunk-list">
              {shown.map((chunk) => {
                const active = highlight?.id === chunk.id;
                return (
                  <li
                    key={chunk.id}
                    id={`chunk-${chunk.id}`}
                    className={`chunk-item ${active ? 'highlight' : ''}`}
                    aria-current={active ? 'true' : undefined}
                    tabIndex={-1}
                  >
                    <div className="chunk-location">
                      {locationText(chunk.page, chunk.paragraph) || `第 ${chunk.ordinal + 1} 段`}
                    </div>
                    <p className="chunk-text">{chunk.text}</p>
                  </li>
                );
              })}
            </ul>
            {chunks.length > visibleChunks && (
              <div className="knowledge-more">
                <Button variant="secondary" onClick={onShowMore}>
                  加载更多片段
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

interface KnowledgeFormProps {
  onSuccess: (source: KnowledgeSource) => void;
  onCancel: () => void;
  onBusyChange?: (busy: boolean) => void;
}

export function KnowledgeForm({ onSuccess, onCancel, onBusyChange }: KnowledgeFormProps) {
  const [kind, setKind] = useState<'text' | 'url' | 'bookmark' | 'file'>('text');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (loading) return;
    setError('');

    try {
      setLoading(true);
      onBusyChange?.(true);
      let source: KnowledgeSource;
      if (kind === 'file') {
        if (!file) {
          setError('请选择文件');
          return;
        }
        source = await api.uploadKnowledgeFile(title.trim() || file.name.slice(0, 500), file);
      } else {
        const fallback = kind === 'text' ? content.trim().split('\n')[0].slice(0, 80) : content.trim().slice(0, 500);
        source = await api.createKnowledge(kind, title.trim() || fallback, content.trim());
      }
      onSuccess(source);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
      onBusyChange?.(false);
    }
  }

  const kinds: { value: typeof kind; label: string }[] = [
    { value: 'text', label: '粘贴文字' },
    { value: 'url', label: '添加链接' },
    { value: 'file', label: '上传文件' },
  ];

  return (
    <Card>
      <form onSubmit={handleSubmit} className="knowledge-form" noValidate>
        {error && (
          <div className="knowledge-form-error" role="alert">
            {error}
          </div>
        )}

        <fieldset className="knowledge-form-kind">
          <legend className="input-label">类型</legend>
          {kinds.map((item) => (
            <label key={item.value}>
              <input
                type="radio"
                name="knowledge-kind"
                value={item.value}
                checked={kind === item.value}
                onChange={() => setKind(item.value)}
              />
              {item.label}
            </label>
          ))}
        </fieldset>

        <Input
          label="标题（选填）"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        {kind === 'text' && (
          <Textarea
            label="正文"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
            rows={8}
          />
        )}

        {(kind === 'url' || kind === 'bookmark') && (
          <Input
            label="URL"
            type="url"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
          />
        )}

        {kind === 'file' && (
          <Input
            label="文件"
            type="file"
            accept=".pdf,.txt,.md"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        )}

        <div className="knowledge-form-actions">
          <Button type="button" variant="secondary" disabled={loading} onClick={onCancel}>
            取消
          </Button>
          <Button type="submit" loading={loading}>
            添加
          </Button>
        </div>
      </form>
    </Card>
  );
}
