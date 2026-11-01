export default function DataAccessNotice({
  requirement,
}: {
  requirement: string;
}) {
  return (
    <div className="card p-5 text-body text-slate" role="status">
      This section requires {requirement}. Ask your organization owner for an
      explicit Data Engine grant.
    </div>
  );
}
