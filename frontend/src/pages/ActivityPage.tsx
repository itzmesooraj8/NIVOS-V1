import { Activity } from '../components/Dashboard/TraceDebugger';

export function ActivityPage() {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-5xl mx-auto">
        <Activity />
      </div>
    </div>
  );
}
