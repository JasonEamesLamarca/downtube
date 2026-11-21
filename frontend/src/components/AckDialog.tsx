import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../api/endpoints';
import { ME_KEY } from '../auth';
import { ErrorNote, Modal } from './ui';

export function AckDialog() {
  const qc = useQueryClient();
  const [checked, setChecked] = useState(false);
  const m = useMutation({ mutationFn: endpoints.acknowledge, onSuccess: (me) => qc.setQueryData(ME_KEY, me) });
  return (
    <Modal title="Before you start archiving" dismissible={false}
      footer={<button className="btn btn-primary" disabled={!checked || m.isPending} onClick={() => m.mutate()}>Continue</button>}>
      <p>DownTube archives videos to storage you control. Archiving content requires that you have the appropriate rights or permission to make copies of it.</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>You are responsible for how you use DownTube and for complying with applicable laws and the terms of the services involved.</li>
        <li>DownTube never bypasses DRM, paywalls, geo-restrictions, or login restrictions. Videos that need those are reported as unsupported or failed.</li>
      </ul>
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        <span>I understand and confirm that I have the rights or permission to archive the content I select.</span>
      </label>
      <ErrorNote error={m.error} />
    </Modal>
  );
}
