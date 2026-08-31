#include "AureliaDriveHud.h"

#include "AureliaDriveGameMode.h"
#include "AureliaVehiclePawn.h"
#include "CanvasItem.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"

void AAureliaDriveHud::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas || !GEngine)
	{
		return;
	}

	UFont* Font = GEngine->GetMediumFont();
	const AAureliaVehiclePawn* Car = Cast<AAureliaVehiclePawn>(GetOwningPawn());
	const AAureliaDriveGameMode* Mode = GetWorld() ? GetWorld()->GetAuthGameMode<AAureliaDriveGameMode>() : nullptr;

	const FString Title = TEXT("AURELIA DRIVE");
	FCanvasTextItem TitleItem(FVector2D(32.f, 28.f), FText::FromString(Title), Font, FLinearColor(1.f, 0.77f, 0.54f));
	TitleItem.Scale = FVector2D(1.35f, 1.35f);
	Canvas->DrawItem(TitleItem);

	if (Car)
	{
		const FString Speed = FString::Printf(TEXT("%d"), FMath::RoundToInt(Car->GetSpeedKmh()));
		FCanvasTextItem SpeedItem(FVector2D(32.f, Canvas->SizeY - 110.f), FText::FromString(Speed), Font, FLinearColor::White);
		SpeedItem.Scale = FVector2D(2.4f, 2.4f);
		Canvas->DrawItem(SpeedItem);

		FCanvasTextItem UnitItem(FVector2D(32.f, Canvas->SizeY - 48.f), FText::FromString(TEXT("KM/H   WASD  SPACE  R RESET")), Font, FLinearColor(0.49f, 0.94f, 1.f));
		Canvas->DrawItem(UnitItem);
	}

	if (Mode)
	{
		FCanvasTextItem LapItem(FVector2D(Canvas->SizeX - 280.f, 28.f), FText::FromString(Mode->GetLapText()), Font, FLinearColor::White);
		LapItem.Scale = FVector2D(1.2f, 1.2f);
		Canvas->DrawItem(LapItem);

		FCanvasTextItem StatusItem(FVector2D(Canvas->SizeX - 420.f, Canvas->SizeY - 48.f), FText::FromString(Mode->GetStatusText()), Font, FLinearColor(1.f, 0.77f, 0.54f));
		Canvas->DrawItem(StatusItem);
	}
}
