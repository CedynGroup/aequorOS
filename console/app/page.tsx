import { redirect } from 'next/navigation';

/** The console's home is the tenant inventory. */
export default function Home() {
  redirect('/tenants');
}
