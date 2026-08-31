#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "AureliaDriveGameMode.generated.h"

/**
 * Spawns dusk coastal lighting (Sky Atmosphere + low sun) so Lumen has
 * a GTA-6-class golden hour to work with. Original setup. No Rockstar assets.
 */
UCLASS()
class AURELIADRIVE_API AAureliaDriveGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AAureliaDriveGameMode();

	virtual void BeginPlay() override;
};
