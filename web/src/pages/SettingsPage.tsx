import { ChevronRight, FileText, Heart, Activity } from 'lucide-react';
import './SettingsPage.css';

export function SettingsPage() {
  return <div className="settings-page">
    <h1>设置</h1>
    <div className="settings-links">
      {[
        { href: '/preferences', label: '旅行偏好', Icon: Heart },
        { href: '/knowledge', label: '参考资料', Icon: FileText },
        { href: '/status', label: '服务状态', Icon: Activity },
      ].map(({ href, label, Icon }) => <a href={href} key={href}>
        <Icon size={20} aria-hidden="true" /><span>{label}</span><ChevronRight size={18} aria-hidden="true" />
      </a>)}
    </div>
  </div>;
}
