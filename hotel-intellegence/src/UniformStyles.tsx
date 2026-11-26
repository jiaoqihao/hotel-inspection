type Props = {
  tie: string;
  hair: string;
  onTie: (value: string) => void;
  onHair: (value: string) => void;
};

export default function UniformStyles({tie, hair, onTie, onHair}: Props) {
  return <fieldset className="uniform-styles">
    <legend>举手仪容检查</legend>
    <p>全身正对镜头，单手举过肩保持半秒。随后可放下手，向左或向右慢慢转一圈，在 12 秒内重新面向镜头。仅上半身入镜或柜台挡住下半身时，举手无效。全身转完才生成记录。</p>
    <div className="vision-fields">
      <label>男士工服特征<input maxLength={400} value={tie} onChange={e=>onTie(e.target.value)} placeholder="例如：藏青色翻领西装外套"/></label>
      <label>女士工服特征<input maxLength={400} value={hair} onChange={e=>onHair(e.target.value)} placeholder="例如：黑色收腰西装外套"/></label>
    </div>
    <small>上身工服只检查是否穿西装外套。系统根据举手者的正面画面选择适用规范：男士检查领带，女士检查扎发，短发免扎。皮鞋看露出部分，大致符合深色皮鞋外观且无明显污渍即可。</small>
  </fieldset>;
}
