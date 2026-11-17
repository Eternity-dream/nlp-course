import { useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { api } from '../api';
import type { KnowledgeHit } from '../types';
import { Card } from './Card';
import { Button } from './Button';
import { Input } from './Input';
import { formatApiError } from '../utils/errors';

interface KnowledgeSearchProps {
  onOpen: (sourceId: string, hit: KnowledgeHit) => void;
}

export function KnowledgeSearch({ onOpen }: KnowledgeSearchProps) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const seq = useRef(0);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const text = query.trim();
    if (!text) return;
    const current = ++seq.current;
    setLoading(true);
    setError('');
    try {
      const results = await api.searchKnowledge(text);
      if (current !== seq.current) return;
      setHits(results);
    } catch (err) {
      if (current !== seq.current) return;
      setHits(null);
      setError(formatApiError(err));
    } finally {
      if (current === seq.current) setLoading(false);
    }
  }

  return (
    <Card>
      <form onSubmit={handleSearch} className="knowledge-search-form" role="search">
        <Input
          aria-label="搜索资料"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" loading={loading} disabled={!query.trim()}>
          <Search size={16} />
          搜索
        </Button>
      </form>

      {error && (
        <div className="knowledge-error" role="alert">
          {error}
        </div>
      )}

      {hits !== null && hits.length === 0 && (
        <div className="knowledge-search-empty">未找到匹配的资料</div>
      )}

      {hits !== null && hits.length > 0 && (
        <ul className="hit-list">
          {hits.map((hit) => (
            <li key={hit.id}>
              <button
                type="button"
                className="hit-item"
                onClick={() => onOpen(hit.source_id, hit)}
                aria-label={`打开资料：${hit.title}`}
              >
                <span className="hit-title">{hit.title}</span>
                {(hit.page != null || hit.paragraph != null) && (
                  <span className="hit-location">
                    {hit.page != null && `第 ${hit.page} 页`}
                    {hit.page != null && hit.paragraph != null && ' · '}
                    {hit.paragraph != null && `第 ${hit.paragraph} 段`}
                  </span>
                )}
                <span className="hit-text">{hit.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
