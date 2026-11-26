type Props = {
  tie: string;
  hair: string;
  onTie: (value: string) => void;
  onHair: (value: string) => void;
};

export default function UniformStyles({tie, hair, onTie, onHair}: Props) {
  return <fieldset className="uniform-styles">
    <legend>举手仪容检查</legend>
    <p>面向镜头，将一只手举过肩并保持半秒。检查覆盖整个画面，多人举手可分别取证。</p>
    <div className="vision-fields">
      <label>领带款工服特征<input maxLength={400} value={tie} onChange={e=>onTie(e.target.value)} placeholder="例如：藏青色西装、白色尖领衬衫"/></label>
      <label>扎发款工服特征<input maxLength={400} value={hair} onChange={e=>onHair(e.target.value)} placeholder="例如：酒红色立领外套、金色滚边"/></label>
    </div>
    <small>两款均检查工服和胸牌。请填写能区分款式的特征；款式不清时交由人工复核。扎发款短发免扎。</small>
  </fieldset>;
}
