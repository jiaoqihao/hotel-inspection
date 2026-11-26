import type { ReactNode } from 'react';
import { Camera, Headphones, Phone, ClipboardList, type LucideIcon } from 'lucide-react';

export const workAreas = [
  { page: 'vision', label: '摄像头巡检', hint: '巡检点位、影像取证与整改', Icon: Camera },
  { page: 'badge', label: '挂牌与胸牌语音', hint: '设备、录音档案与服务复核', Icon: Headphones },
  { page: 'phone', label: '前台电话', hint: '通话记录、客需与工单', Icon: Phone },
];

export function WorkHeading({ title, description, children }: {
  title: string; description?: string; children?: ReactNode;
}) {
  return <header className="work-heading">
    <div><h1>{title}</h1>{description && <p>{description}</p>}</div>
    {children}
  </header>;
}

export function EmptyState({ title, detail, icon: Icon = ClipboardList }: {
  title: string; detail?: string; icon?: LucideIcon;
}) {
  return <div className="work-empty">
    <span className="work-icon"><Icon size={22} aria-hidden="true"/></span>
    <strong>{title}</strong>{detail && <p>{detail}</p>}
  </div>;
}

export function StateTag({ value }: { value: string }) {
  const tone = ['已完成','已确认','已复查','等待下次巡检'].includes(value) ? 'green'
    : ['待整改','待复核','到期候检','等待或正在分析','抓帧中','分析中'].includes(value) ? 'amber'
    : value.includes('失败') ? 'red' : 'neutral';
  return <span className={`tag ${tone}`}>{value}</span>;
}
