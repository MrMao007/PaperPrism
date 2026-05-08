import { useCallback, useState } from 'react';

export type DialogButtonVariant = 'default' | 'danger' | 'primary';

export interface DialogButton {
  label: string;
  variant?: DialogButtonVariant;
  onClick: () => void;
}

export interface DialogProps {
  title: string;
  message: string;
  buttons: DialogButton[];
  /** When true, clicking the backdrop closes the dialog (calls last button). */
  closeOnBackdrop?: boolean;
  onClose?: () => void;
}

export function Dialog({ title, message, buttons, closeOnBackdrop = false, onClose }: DialogProps) {
  function handleBackdrop() {
    if (closeOnBackdrop && onClose) onClose();
  }

  return (
    <div className="dlg-backdrop" onMouseDown={handleBackdrop}>
      <div
        className="dlg-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dlg-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <p id="dlg-title" className="dlg-title">{title}</p>
        <p className="dlg-message">{message}</p>
        <div className="dlg-actions">
          {buttons.map((btn) => (
            <button
              key={btn.label}
              type="button"
              className={`dlg-btn dlg-btn--${btn.variant ?? 'default'}`}
              onClick={btn.onClick}
            >
              {btn.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

interface UseDialogState {
  props: DialogProps | null;
}

export function useDialog() {
  const [state, setState] = useState<UseDialogState>({ props: null });

  const dismiss = useCallback(() => setState({ props: null }), []);

  /** Drop-in replacement for window.alert — resolves when user closes the dialog. */
  const showAlert = useCallback((title: string, message: string): Promise<void> => {
    return new Promise((resolve) => {
      setState({
        props: {
          title,
          message,
          closeOnBackdrop: true,
          buttons: [{ label: 'OK', variant: 'primary', onClick: () => { dismiss(); resolve(); } }],
          onClose: () => { dismiss(); resolve(); },
        },
      });
    });
  }, [dismiss]);

  /** Drop-in replacement for window.confirm — resolves true/false. */
  const showConfirm = useCallback((
    title: string,
    message: string,
    confirmLabel = 'Confirm',
    confirmVariant: DialogButtonVariant = 'danger',
  ): Promise<boolean> => {
    return new Promise((resolve) => {
      setState({
        props: {
          title,
          message,
          closeOnBackdrop: false,
          buttons: [
            { label: 'Cancel',     variant: 'default',       onClick: () => { dismiss(); resolve(false); } },
            { label: confirmLabel, variant: confirmVariant,  onClick: () => { dismiss(); resolve(true); } },
          ],
        },
      });
    });
  }, [dismiss]);

  const dialogNode = state.props ? <Dialog {...state.props} /> : null;

  return { dialogNode, showAlert, showConfirm };
}
