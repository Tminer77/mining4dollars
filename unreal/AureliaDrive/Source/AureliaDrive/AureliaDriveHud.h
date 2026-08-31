#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "AureliaDriveHud.generated.h"

UCLASS()
class AURELIADRIVE_API AAureliaDriveHud : public AHUD
{
	GENERATED_BODY()

public:
	virtual void DrawHUD() override;
};
