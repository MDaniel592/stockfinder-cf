import { Cpu, Gpu, CircuitBoard, MemoryStick, HardDrive, PlugZap, PcCase, Fan, Layers } from 'lucide-react';
const icons = { cpu: Cpu, gpu: Gpu, motherboard: CircuitBoard, ram: MemoryStick,
  storage: HardDrive, psu: PlugZap, case: PcCase, cooler: Fan };
export function CategoryIcon({ category }: { category: string }) {
  const Icon = icons[category as keyof typeof icons] ?? Layers;
  return <Icon className="category-icon" size={19} strokeWidth={1.6} aria-hidden="true"/>;
}
