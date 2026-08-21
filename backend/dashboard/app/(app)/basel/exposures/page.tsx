'use client';

import { useBankContext } from '@/components/shell/BankContext';
import SdiExposureView from '@/components/basel/SdiExposureView';

export default function SdiExposuresPage() {
  const { bank } = useBankContext();
  return <SdiExposureView bankId={bank?.id} />;
}