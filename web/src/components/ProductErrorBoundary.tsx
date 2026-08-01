import { Component, type ErrorInfo, type ReactNode } from "react";

interface ProductErrorBoundaryProps {
  children: ReactNode;
}

interface ProductErrorBoundaryState {
  failed: boolean;
}

export class ProductErrorBoundary extends Component<
  ProductErrorBoundaryProps,
  ProductErrorBoundaryState
> {
  state: ProductErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ProductErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ControlStudio UI failed safely.", error, info);
  }

  render(): ReactNode {
    if (!this.state.failed) {
      return this.props.children;
    }

    return (
      <main className="fatal-error-screen" aria-labelledby="fatal-error-title">
        <p className="eyebrow">CONTROLSTUDIO UI RECOVERY</p>
        <h1 id="fatal-error-title">화면을 복구해야 합니다</h1>
        <p>
          Host와 진행 중인 작업은 자동으로 다시 실행하지 않습니다. 화면만 새로
          불러와 서버에 저장된 현재 상태를 확인하세요.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          화면 다시 불러오기
        </button>
      </main>
    );
  }
}
