import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { ConfigStatus, RuntimeStatus, ServiceCheck } from '../types';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Badge } from '../components/Badge';
import { Loading } from '../components/Loading';
import { formatApiError } from '../utils/errors';
import './StatusPage.css';

const SERVICE_LABELS: Record<string, string> = {
  llm: '模型',
  search: '搜索',
  amap: '地图',
};

const PROVIDER_LABELS: Record<string, string> = {
  exa: 'Exa',
  tavily: 'Tavily',
  anysearch: 'AnySearch',
};

const CONFIG_FIELD_LABELS: Record<string, string> = {
  LLM_BASE_URL: '模型地址',
  LLM_API_KEY: '模型密钥',
  LLM_MODEL: '模型名称',
  AMAP_API_KEY: '地图密钥',
};

export function StatusPage() {
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [checks, setChecks] = useState<ServiceCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    loadStatus();
  }, []);

  async function loadStatus() {
    try {
      setLoading(true);
      setError('');
      const [configData, runtimeData, checksData] = await Promise.all([
        api.getConfigStatus(),
        api.getRuntime(),
        api.listServiceChecks(),
      ]);
      setConfig(configData);
      setRuntime(runtimeData);
      setChecks(checksData);
    } catch (err: any) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleCheck(service: 'llm' | 'search' | 'amap') {
    try {
      setChecking(service);
      setError('');
      await api.checkService(service);
      await loadStatus();
    } catch (err: any) {
      setError(formatApiError(err));
    } finally {
      setChecking(null);
    }
  }

  if (loading) {
    return <Loading text="加载中" />;
  }

  if (error && !config) {
    return (
      <div className="status-page">
        <div className="status-header">
          <h1 className="status-title">服务状态</h1>
        </div>
        <Card>
          <div className="status-error">{error}</div>
          <Button onClick={loadStatus} style={{ marginTop: 'var(--spacing-12)' }}>
            重试
          </Button>
        </Card>
      </div>
    );
  }

  if (!config || !runtime) {
    return null;
  }

  return (
    <div className="status-page">
      <div className="status-header">
        <h1 className="status-title">服务状态</h1>
        <Button variant="secondary" onClick={loadStatus}>
          <RefreshCw size={16} />
          刷新
        </Button>
      </div>

      {error && <div className="status-error">{error}</div>}

      <Card>
        <div className="status-section">
          <h3 className="status-section-title">配置状态</h3>
          <div className="status-item">
            <span className="status-item-label">规划就绪</span>
            <StatusBadge success={config.ready_for_planning} />
          </div>
          {config.missing.length > 0 && (
            <div className="status-missing">
              <div className="status-missing-title">缺少配置</div>
              {config.missing.map((item) => (
                <div key={item} className="status-missing-item">
                  {CONFIG_FIELD_LABELS[item] || item}
                </div>
              ))}
            </div>
          )}
          {config.errors.length > 0 && (
            <div className="status-errors">
              <div className="status-errors-title">配置错误</div>
              {config.errors.map((err, i) => (
                <div key={i} className="status-errors-item">
                  {err}
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="status-section">
          <h3 className="status-section-title">模型</h3>
          <div className="status-item">
            <span className="status-item-label">配置</span>
            <Badge variant={config.llm.configured ? 'success' : 'danger'}>
              {config.llm.configured ? '已配置' : '未配置'}
            </Badge>
          </div>
          <Button
            size="sm"
            onClick={() => handleCheck('llm')}
            loading={checking === 'llm'}
            disabled={!config.llm.configured}
          >
            检查
          </Button>
        </div>
      </Card>

      <Card>
        <div className="status-section">
          <h3 className="status-section-title">搜索</h3>
          {config.search.map((provider) => (
            <div key={provider.provider} className="status-provider">
              <div className="status-provider-name">
                {PROVIDER_LABELS[provider.provider] || '搜索服务'}
              </div>
              <Badge variant={provider.configured ? 'success' : 'info'}>
                {provider.configured ? '已配置' : '未配置'}
              </Badge>
            </div>
          ))}
          <Button
            size="sm"
            onClick={() => handleCheck('search')}
            loading={checking === 'search'}
            disabled={config.search.length === 0}
          >
            检查
          </Button>
        </div>
      </Card>

      <Card>
        <div className="status-section">
          <h3 className="status-section-title">地图</h3>
          <div className="status-item">
            <span className="status-item-label">配置</span>
            <Badge variant={config.amap.configured ? 'success' : 'danger'}>
              {config.amap.configured ? '已配置' : '未配置'}
            </Badge>
          </div>
          <Button
            size="sm"
            onClick={() => handleCheck('amap')}
            loading={checking === 'amap'}
            disabled={!config.amap.configured}
          >
            检查
          </Button>
        </div>
      </Card>

      {runtime.idle !== undefined && (
        <Card>
          <div className="status-section">
            <h3 className="status-section-title">运行状态</h3>
            <div className="status-item">
              <span className="status-item-label">空闲</span>
              <Badge variant={runtime.idle ? 'success' : 'info'}>
                {runtime.idle ? '是' : '否'}
              </Badge>
            </div>
          </div>
        </Card>
      )}

      {checks.length > 0 && (
        <Card>
          <h3 className="status-section-title">检查历史</h3>
          <div className="status-checks">
            {checks.slice(0, 5).map((check) => (
              <div key={check.id} className="status-check">
                <div className="status-check-header">
                  <span className="status-check-service">
                    {SERVICE_LABELS[check.service] || '服务'}
                  </span>
                  <StatusBadge success={check.success} />
                </div>
                <div className="status-check-time">
                  {new Date(check.checked_at).toLocaleString('zh-CN')}
                </div>
                {!check.success && check.details?.error?.message && (
                  <div className="status-check-error">
                    {formatApiError({ message: check.details.error.message })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function StatusBadge({ success }: { success: boolean }) {
  return (
    <Badge variant={success ? 'success' : 'danger'} showDot={false}>
      {success ? (
        <>
          <CheckCircle size={12} />
          通过
        </>
      ) : (
        <>
          <XCircle size={12} />
          失败
        </>
      )}
    </Badge>
  );
}
