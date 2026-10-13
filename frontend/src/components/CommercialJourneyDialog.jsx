import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import CommercialJourneyTimeline from "./CommercialJourneyTimeline";
import "../styles/journey-dialog.css";

// A portal belongs to the existing React root. The native top layer isolates
// keyboard/pointer interaction without unmounting the Inbox or its socket.
export default function CommercialJourneyDialog({ empresaId, leadId, contactName, onClose, returnFocusRef }) {
  const dialogRef = useRef(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    // showModal focuses the first focusable descendant (the close button).
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus({ preventScroll: true });
    };
  }, [returnFocusRef]);

  return createPortal(
    <dialog ref={dialogRef} aria-labelledby="journey-dialog-title" aria-modal="true" className="journey-reader" data-testid="journey-dialog"
      onCancel={event => { event.preventDefault(); onClose(); }}>
      <CommercialJourneyTimeline empresaId={empresaId} leadId={leadId} contactName={contactName} onClose={onClose} />
    </dialog>,
    document.body,
  );
}
