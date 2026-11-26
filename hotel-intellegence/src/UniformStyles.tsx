type Props = {
  tie: string;
  hair: string;
  onTie: (value: string) => void;
  onHair: (value: string) => void;
};

export default function UniformStyles({tie, hair, onTie, onHair}: Props) {
  return <fieldset className="uniform-styles">
    <legend>举手仪容检查</legend>
    <p>从头到脚正对镜头，单手举过肩保持半秒，即可生成一条仪容检查记录。双手放下约一秒后可再次举手。</p>
    <div className="vision-fields">
      <label>男士工服特征<input maxLength={400} value={tie} onChange={e=>onTie(e.target.value)} placeholder="例如：黑色翻领西装外套"/></label>
      <label>女士工服特征<input maxLength={400} value={hair} onChange={e=>onHair(e.target.value)} placeholder="例如：黑色收腰西装外套"/></label>
    </div>
    <small>工服检查西装外套及点位配置的颜色，胸牌检查本人左上胸的长方形工牌。系统根据举手者的正面画面选择适用规范：男士检查领带，女士检查扎发，短发免扎。皮鞋看露出部分，大致符合深色皮鞋外观且无明显污渍即可。</small>
  </fieldset>;
}
